#!/usr/bin/env bash
# Тестовый стенд сайта: изолированный стек (своя БД, порты 8001/8081/8444).
#   bash deploy/teststand.sh          — поднять/обновить + залить демо-данные
#   bash deploy/teststand.sh nuke     — снести стенд вместе с данными
set -euo pipefail
cd "$(dirname "$0")/.."

P="-p auto_bot_test"
F=(-f docker-compose.yml -f deploy/docker-compose.test.yml)

if [ "${1:-}" = nuke ]; then
  docker compose $P "${F[@]}" down -v
  echo "Тестовый стенд снесён (с данными)."
  exit 0
fi

echo "[1/5] Подъём тестового стенда (своя БД проекта auto_bot_test)..."
docker compose $P "${F[@]}" --profile mono up -d postgres redis backend miniapp

echo "[2/5] Ожидание backend (:8001)..."
for _ in $(seq 1 30); do
  curl -fs http://localhost:8001/health >/dev/null 2>&1 && break
  sleep 2
done
curl -fs http://localhost:8001/health >/dev/null || { echo "backend не поднялся"; exit 1; }

echo "[3/5] Миграции (свежая БД)..."
docker compose $P "${F[@]}" exec -T backend alembic upgrade head

echo "[4/5] Демо-данные: копия listings+history из боевой БД (не трогаем её)..."
docker compose $P "${F[@]}" exec -T backend python -c "
import asyncio
from sqlalchemy import text
from app.db.session import engine
async def main():
    async with engine.begin() as c:
        n = await c.scalar(text('SELECT count(*) FROM listings'))
        print('listings в тестовой БД:', n)
asyncio.run(main())
" | grep -q 'listings в тестовой БД: 0' && \
docker exec auto_bot-postgres-1 pg_dump -U app -d autoselect --data-only \
  -t listings -t listing_price_history 2>/dev/null | \
docker compose $P "${F[@]}" exec -T postgres psql -q -U app -d autoselect \
  || echo "(данные уже залиты или боевая БД недоступна — пропускаю)"

echo "[5/5] Готово!"
echo
echo "  Сайт (гостевой режим):  https://localhost:8444   (HTTP: http://localhost:8081)"
IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[ -z "$IP" ] && IP="$(ipconfig 2>/dev/null | grep -oE '10\.[0-9.]+|192\.168\.[0-9.]+' | head -1 || true)"
[ -n "$IP" ] && echo "  Из локальной сети:      https://$IP:8444"
echo "  API:                    http://localhost:8001/health  (LLM: mock — без расхода токенов)"
echo "  Снести: bash deploy/teststand.sh nuke"
