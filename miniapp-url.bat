@echo off
rem ============================================================
rem auto_bot — публичный URL миниаппа для Telegram.
rem
rem Telegram открывает web_app ТОЛЬКО по https с доверенным
rem сертификатом: self-signed из entrypoint-ssl.sh работает в
rem браузере на LAN, но Telegram такой сайт молча не покажет.
rem
rem Скрипт поднимает SSH-туннель к localhost.run (бесплатно, без
rem регистрации, валидный TLS на их edge), выцепляет выданный
rem URL, прописывает его в MINIAPP_URL (.env) и пересоздаёт бота
rem — кнопку web_app бот ставит при старте.
rem
rem ВНИМАНИЕ: URL случайный и меняется:
rem   - при повторном запуске этого скрипта;
rem   - при обрыве ssh-сессии (кнопка будет вести на мёртвый
rem     адрес — просто запусти скрипт ещё раз).
rem Для постоянного URL: бесплатный аккаунт localhost.run (домен
rem вида name.lhr.life) либо именованный Cloudflare Tunnel,
rem если сеть пропускает trycloudflare (у нас — НЕ пропускает).
rem ============================================================
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo [1/4] Завершаю прошлый туннель (если был)...
if exist .miniapp_tunnel.pid (
  for /f %%p in (.miniapp_tunnel.pid) do taskkill /PID %%p /T /F >nul 2>&1
  del .miniapp_tunnel.pid >nul 2>&1
)
rem лог чистим всегда: парсер берёт ПЕРВЫЙ URL в файле, а старые
rem записи указывают на уже убитые туннели
del .miniapp_tunnel.log >nul 2>&1

echo [2/4] Поднимаю туннель localhost.run (проброс на nginx :8080)...
rem Туннель в отдельном ps1: цикл с автоперезапуском ssh (git-овский),
rem лог/ошибки в .miniapp_tunnel.log/.err, PID в .miniapp_tunnel.pid.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','scripts\miniapp_tunnel.ps1' -RedirectStandardOutput '.miniapp_tunnel_loop.log' -RedirectStandardError '.miniapp_tunnel.err' -WindowStyle Hidden -PassThru; $p.Id | Set-Content .miniapp_tunnel.pid" || goto :err

echo [3/4] Ожидание URL (до ~60с)...
set URL=
set /a i=0
:waitloop
set /a i+=1
if %i% gtr 30 goto :err
for /f "usebackq delims=" %%u in (`powershell -NoProfile -Command "$m = Get-Content .miniapp_tunnel.log -ErrorAction SilentlyContinue | Select-String -Pattern 'https://[a-z0-9-]+\.lhr\.life' -AllMatches; if ($m) { $m.Matches.Value | Select-Object -First 1 }"`) do set URL=%%u
if "%URL%"=="" (timeout /t 2 /nobreak >nul & goto :waitloop)

echo [4/4] MINIAPP_URL=%URL% в .env, пересоздаю бота...
powershell -NoProfile -Command "$c = Get-Content .env; if ($c -match '^MINIAPP_URL=') { $c = $c -replace '^MINIAPP_URL=.*', ('MINIAPP_URL=' + '%URL%') } else { $c += ('MINIAPP_URL=' + '%URL%') }; Set-Content .env -Value $c -Encoding utf8" || goto :err
rem --no-deps: не трогать зависимости (иначе пересоздастся gateway)
docker compose up -d --force-recreate --no-deps bot || goto :err

echo.
echo   Готово! Миниапп доступен из интернета (валидный TLS):
echo     %URL%
echo   Кнопка в Telegram обновится в течение ~минуты (кэш клиента)
echo   или после перезапуска чата с ботом.
echo   Туннель живёт, пока жив этот процесс (PID в .miniapp_tunnel.pid).
goto :eof

:err
echo ОШИБКА — см. .miniapp_tunnel.log / .miniapp_tunnel.err
goto :eof
