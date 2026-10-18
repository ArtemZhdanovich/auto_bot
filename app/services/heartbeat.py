"""Heartbeat-файл: liveness для контейнеров без HTTP-порта (bot, воркеры).

Каждые HEARTBEAT_INTERVAL_SEC секунд процесс обновляет heartbeat-файл;
healthcheck в docker-compose (`python -m app.services.heartbeat <name>`)
считает процесс живым, пока файлу меньше HEARTBEAT_MAX_AGE_SEC. Зависший
event loop (polling/консюмер встал) перестаёт обновлять файл — контейнер
уходит в unhealthy и перезапускается (restart: unless-stopped).
"""

import asyncio
import logging
import os
import sys
import tempfile
import time

logger = logging.getLogger(__name__)

INTERVAL_SEC = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "15"))
# 6 пропущенных интервалов = процесс завис; с запасом, чтобы не рестартовать
# на короткой блокировке (долгая задача, сборка мусора).
MAX_AGE_SEC = int(os.getenv("HEARTBEAT_MAX_AGE_SEC", "90"))


def heartbeat_path(name: str) -> str:
    return os.path.join(tempfile.gettempdir(), f"heartbeat_{name}")


async def start_heartbeat(name: str) -> asyncio.Task[None]:
    """Фоновая задача: пишет timestamp в heartbeat-файл каждые INTERVAL_SEC.

    Задача живёт до смерти процесса — отдельная отмена не нужна.
    """

    async def _loop() -> None:
        path = heartbeat_path(name)
        while True:
            try:
                with open(path, "w") as fh:
                    fh.write(str(time.time()))
            except OSError:  # файловая система не должна ронять воркер
                logger.warning("heartbeat_write_failed: %s", path)
            await asyncio.sleep(INTERVAL_SEC)

    return asyncio.create_task(_loop(), name=f"heartbeat_{name}")


def is_alive(name: str, max_age: int = MAX_AGE_SEC) -> bool:
    try:
        age = time.time() - os.path.getmtime(heartbeat_path(name))
    except OSError:  # файла нет — процесс ещё не стартовал или умер
        return False
    return age <= max_age


if __name__ == "__main__":
    # CLI для docker healthcheck: `python -m app.services.heartbeat <name>`
    if len(sys.argv) != 2:
        print("usage: python -m app.services.heartbeat <name>", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if is_alive(sys.argv[1]) else 1)
