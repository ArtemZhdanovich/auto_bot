@echo off
rem ============================================================
rem auto_bot — запуск стека на Windows (Docker Desktop).
rem Использование: start.bat [mono|micro]   (по умолчанию mono)
rem ============================================================
setlocal
set PROFILE=%1
if "%PROFILE%"=="" set PROFILE=mono

echo [1/5] Инфраструктура (postgres/redis/rabbitmq)...
docker compose up -d postgres redis rabbitmq || goto :err

echo [2/5] Сборка и запуск (%PROFILE%)...
docker compose --profile %PROFILE% up -d --build || goto :err

echo [3/5] Миграции БД...
docker compose exec -T backend alembic upgrade head || goto :err

echo [4/5] Ожидание backend...
set /a i=0
:waitloop
set /a i+=1
if %i% gtr 30 goto :err
curl -s -o nul http://localhost:8000/health
if errorlevel 1 (timeout /t 2 /nobreak >nul & goto :waitloop)

echo [5/5] Готово!
echo.
echo   API:     http://localhost:8000/health
echo   Миниапп: http://localhost:8080  (HTTPS: https://localhost:8443)
echo   Сайт без Telegram: открой миниапп в браузере — гостевой режим включится сам
exit /b 0
:err
echo ОШИБКА запуска — см. вывод выше / docker compose logs
goto :eof
