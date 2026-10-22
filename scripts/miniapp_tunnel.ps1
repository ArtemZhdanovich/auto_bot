# Туннель миниаппа -> localhost.run с автоперезапуском и health-probe.
# Запускается скрыто из miniapp-url.bat (PID пишется в .miniapp_tunnel.pid);
# вручную: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/miniapp_tunnel.ps1
#
# ДВА нюанса этого провайдера/сети, под которые заточен цикл:
# 1) анонимным сессиям выдаётся СЛУЧАЙНЫЙ поддомен при КАЖДОМ подключении,
#    поэтому после реконнекта парсим новый URL из лога и сами обновляем
#    MINIAPP_URL в .env и пересоздаём бота (--no-deps);
# 2) edge местами теряет РЕГИСТРАЦИЮ туннеля через ~40-60с, при том что
#    ssh-сессия формально жива (keepalive отвечают) и клиент ничего не
#    заметит. Поэтому раз в 25с дёргаем URL (HEAD); два не-200 подряд —
#    убиваем ssh и переподключаемся.
# Используем git-овский ssh: системный OpenSSH (System32) с -R у
# localhost.run работал нестабильно, git-овский — стабильно.
$ErrorActionPreference = 'Continue'
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path $ssh)) { $ssh = 'ssh' }
# .env и docker-compose.yml лежат в корне проекта (на уровень выше scripts/)
Push-Location (Join-Path $PSScriptRoot '..')
$root = (Get-Location).Path

# Один экземпляр на машину. Повторный запуск (планировщик + вручную)
# давал ДВА цикла: второй затирал .miniapp_tunnel.log первого, оба
# держали свои ssh-сессии, .env указывал на мёртвый URL. Диагностика
# осложнялась тем, что «probe -> 200» в логе был мусором из чужой
# итерации. Mutex снимает целый класс таких поломок.
$mutex = New-Object System.Threading.Mutex($false, 'auto_bot_miniapp_tunnel_mutex')
if (-not $mutex.WaitOne(0)) {
  Write-Output 'miniapp_tunnel уже запущен (mutex занят) — второй экземпляр завершается.'
  exit 0
}

# Лог без таймстампов не отвечает на вопрос «когда это произошло» —
# все события пишем через Log.
function Log([string]$msg) {
  Write-Output ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
}

# Пишем свой PID: и bat, и планировщик в итоге убивают дерево по этому файлу.
$PID | Set-Content (Join-Path $root '.miniapp_tunnel.pid')

function Update-MiniappUrl([string]$url) {
  # Меняем MINIAPP_URL в .env только если URL реально сменился.
  $envPath = Join-Path $root '.env'
  $cur = (Select-String -Path $envPath -Pattern '^MINIAPP_URL=(.+)$').Matches.Groups |
         Where-Object { $_.Name -eq '1' } | Select-Object -First 1 -ExpandProperty Value
  if ($cur -eq $url) { return }
  Log "URL сменился: $cur -> $url — обновляю .env и пересоздаю бота..."
  $lines = Get-Content $envPath
  $found = $false
  $new = foreach ($l in $lines) {
    if ($l -match '^MINIAPP_URL=') { $found = $true; "MINIAPP_URL=$url" } else { $l }
  }
  if (-not $found) { $new = @($new) + "MINIAPP_URL=$url" }
  Set-Content $envPath -Value $new -Encoding utf8
  # Бота трогаем, только если контейнер существует (туннель можно запустить
  # и без compose — тогда URL просто ляжет в .env до старта стека).
  if (& docker compose ps -q bot 2>$null) {
    & docker compose up -d --force-recreate --no-deps bot 2>$null | Out-Null
    Log "Бот пересоздан с MINIAPP_URL=$url"
  }
}

while ($true) {
  $log = Join-Path $root '.miniapp_tunnel.log'
  $err = Join-Path $root '.miniapp_tunnel.err'
  Remove-Item $log, $err -ErrorAction SilentlyContinue

  # ssh в фоне: лог стримится в файл, URL парсим из него же (так же его
  # видит miniapp-url.bat — берёт ПЕРВЫЙ URL в файле).
  # SSH_USER: 'nokey' = анонимный туннель (новый поддомен на каждый
  # реконнект); логин аккаунта localhost.run = стабильный поддомен
  # (нужен SSH-ключ, загруженный в admin.localhost.run).
  $sshUser = if ($env:SSH_USER) { $env:SSH_USER } else { 'nokey' }
  $p = Start-Process -FilePath $ssh -ArgumentList @(
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', 'ServerAliveInterval=10',
        '-o', 'ServerAliveCountMax=2',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ConnectTimeout=15',
        '-R', '80:localhost:8080',
        "$sshUser@localhost.run"
      ) -RedirectStandardOutput $log -RedirectStandardError $err -NoNewWindow -PassThru
  Log "ssh запущен (pid $($p.Id))"

  # Ждём URL в логе (баннер localhost.run) до 30с.
  $url = $null
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while (-not $url -and $sw.Elapsed.TotalSeconds -lt 30) {
    Start-Sleep -Milliseconds 800
    if ($p.HasExited) { break }
    $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.lhr\.life' -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if ($m) { $url = $m.Matches[0].Value }
  }
  if (-not $url) {
    Log "ssh не выдал URL за 30с — реконнект через 5с"
    if (-not $p.HasExited) { try { $p.Kill() } catch {} }
    Start-Sleep -Seconds 5
    continue
  }
  Log "Туннель: $url"
  Update-MiniappUrl $url

  # Health-probe: два не-200 подряд считаем потерей туннеля на edge.
  Start-Sleep -Seconds 8   # даём edge дорегистрировать маршрут после баннера
  $fails = 0
  while (-not $p.HasExited) {
    Start-Sleep -Seconds 25
    if ($p.HasExited) { break }
    $code = 0
    try {
      $r = Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 10 -UseBasicParsing
      $code = [int]$r.StatusCode
    } catch {
      if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    }
    Log "probe -> $code"
    if ($code -eq 200) { $fails = 0 } else { $fails++ }
    if ($fails -ge 2) {
      Log "edge потерял туннель — принудительный реконнект"
      try { $p.Kill() } catch {}
      Start-Sleep -Seconds 2
      break
    }
  }
  if ($p.HasExited) { Log "ssh завершился — реконнект через 5с" }
  Start-Sleep -Seconds 5
}
