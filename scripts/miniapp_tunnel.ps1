# Туннель миниаппа -> localhost.run с автоперезапуском.
# Запускается скрыто из miniapp-url.bat (PID пишется в .miniapp_tunnel.pid);
# вручную: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/miniapp_tunnel.ps1
# ssh-сессии localhost.run рвутся (простой, смена сети) — переподключаемся
# через 5с. ВНИМАНИЕ: при переподключении URL может смениться — если кнопка
# в Telegram перестала открываться, перезапусти miniapp-url.bat.
# Используем git-овский ssh: системный OpenSSH (System32) с -R у
# localhost.run работал нестабильно, git-овский — стабильно.
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path $ssh)) { $ssh = 'ssh' }
while ($true) {
  & $ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 `
         -o ExitOnForwardFailure=yes -o ConnectTimeout=15 `
         -R 80:localhost:8080 nokey@localhost.run
  Start-Sleep -Seconds 5
}