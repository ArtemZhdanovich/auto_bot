@echo off
rem ============================================================
rem auto_bot — запуск стека на Windows (Docker Desktop).
rem Использование: start.bat [mono|micro]   (по умолчанию micro)
rem
rem Профили работают ОДИНАКОВО: API всегда на :8000, сайт на
rem :8080/:8443, миграции и healthcheck идут через API-сервис
rem профиля (backend или gateway). При переключении на горячую
rem чужой API-контейнер прибивается автоматически (общие порт
rem :8000 и алиас backend — владелец всегда один), свой стек
rem пересоздаётся compose'ом только если конфиг разнится.
rem ============================================================
cd /d "%~dp0"
setlocal enabledelayedexpansion
set PROFILE=%1
if "%PROFILE%"=="" set PROFILE=micro
if /i "%PROFILE%"=="mono" (
  set PROFILE=mono
  set API_SVC=backend
  set CORE=postgres redis rabbitmq backend bot miniapp worker_fetch worker_send
) else (
  set PROFILE=micro
  set API_SVC=gateway
  set CORE=postgres redis rabbitmq gateway bot miniapp worker_fetch worker_send
)
rem Перекрывает COMPOSE_PROFILES из .env: CLI --profile объединяется с
rem переменной из .env (получили бы два API на одном порту), а переменная
rem окружения процесса имеет приоритет над .env.
set COMPOSE_PROFILES=%PROFILE%

for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^10\.' -or $_.IPAddress -match '^192\.168\.' -or $_.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.' } | Where-Object { $_.InterfaceAlias -notmatch 'Loopback|WSL|vEthernet|Docker' } | Select-Object -First 1 -ExpandProperty IPAddress)"') do set LAN_IP=%%i

echo [1/5] Инфраструктура...
docker compose up -d postgres redis rabbitmq >nul 2>&1 || goto :err

rem Горячее переключение профиля: прибить API-контейнер чужого профиля.
rem Свой поднимется на шаге [2/5]; compose пересоздаст его сам, только
rem если его конфиг разнится с файлом (иначе просто оставит жить).
if /i "%PROFILE%"=="micro" (
  docker compose rm -sf backend >nul 2>&1
) else (
  docker compose rm -sf gateway >nul 2>&1
)

echo [2/5] Сборка и запуск (%PROFILE%)...
docker compose up -d --build %CORE% 2>nul || docker compose up -d %CORE% || goto :err

echo [3/5] Ожидание API (%API_SVC%, порт 8000)...
set /a i=0
:waitloop
set /a i+=1
if %i% gtr 30 goto :err
curl -s -o nul http://localhost:8000/health 2>nul
if errorlevel 1 (timeout /t 2 /nobreak >nul & goto :waitloop)

echo [4/5] Миграции БД (%API_SVC%)...
docker compose exec -T %API_SVC% alembic upgrade head || goto :err

echo [5/5] Готово! (профиль: %PROFILE%, API-сервис: %API_SVC%)
echo.
echo   --- На этой машине ---
echo   API:     http://localhost:8000/health
echo   Сайт:    https://localhost:8443   (HTTP: http://localhost:8080)
if defined LAN_IP (
  echo.
  echo   --- Из локальной сети ---
  echo   Сайт:    https://%LAN_IP%:8443  — серт self-signed, принять риск
  echo   API:     http://%LAN_IP%:8000/health
)
echo.
echo   WebApp-кнопка бота ведёт на MINIAPP_URL из .env
echo   Опц. фичи: docker compose up -d qdrant minio translate rag_api rag_worker
echo   Логи: docker compose logs -f %API_SVC% bot
goto :eof

:err
echo ОШИБКА запуска — см. вывод выше / docker compose logs
goto :eof
