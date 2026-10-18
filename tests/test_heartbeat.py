"""Heartbeat: liveness-файл для bot/воркеров без HTTP-порта."""

import asyncio
import time

from app.services.heartbeat import heartbeat_path, is_alive, start_heartbeat


async def test_is_alive_false_without_file():
    # Файла нет: процесс не стартовал или умер → unhealthy
    assert is_alive("nonexistent_test") is False


async def test_heartbeat_task_keeps_alive():
    path = heartbeat_path("test_alive")
    task = await start_heartbeat("test_alive")
    try:
        await asyncio.sleep(0.05)  # первый тик записывает файл сразу
        assert is_alive("test_alive") is True
        # «Протухший» файл (старше MAX_AGE) → unhealthy
        import os

        old = time.time() - 10_000
        os.utime(path, (old, old))
        assert is_alive("test_alive") is False
    finally:
        task.cancel()
