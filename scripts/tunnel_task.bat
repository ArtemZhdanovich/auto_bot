@echo off
rem ============================================================
rem Обёртка для Планировщика задач (schtasks): поднимает туннель
rem миниаппа ВНЕ сессии терминала — переживает закрытие консоли.
rem Ручной запуск юзером — всё ещё через miniapp-url.bat.
rem Запись задачи:
rem   schtasks /create /f /tn auto_bot_miniapp_tunnel ^
rem     /tr "C:\...\scripts\tunnel_task.bat" /sc once /st 23:59
rem   schtasks /run /tn auto_bot_miniapp_tunnel
rem Остановка: taskkill по .miniapp_tunnel.pid (пишет сам ps1).
rem ============================================================
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\miniapp_tunnel.ps1 > .miniapp_tunnel_loop.log 2> .miniapp_tunnel.err
