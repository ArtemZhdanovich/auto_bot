#!/usr/bin/env bash
# auto_bot — запуск стека (Linux/macOS; на VPS используй deploy/bootstrap.sh).
# Использование: ./start.sh [mono|micro]   (по умолчанию mono)
set -euo pipefail
cd "$(dirname "$0")"
PROFILE="${1:-mono}"

echo "[1/4] Инфраструктура (postgres/redis/rabbitmq)..."
docker compose up -d postgres redis rabbitmq

echo "[2/4] Сборка и запуск ($PROFILE)..."
docker compose --profile "$PROFILE" up -d --build

echo "[3/4] Миграции БД..."
docker compose exec -T backend alembic upgrade head

echo "[4/4] Ожидание backend..."
for _ in $(seq 1 30); do
  curl -fs http://localhost:8000/health >/dev/null 2>&1 && break
  sleep 2
done
curl -fs http://localhost:8000/health >/dev/null || { echo "backend не поднялся — docker compose logs backend"; exit 1; }

echo "Готово!"
echo "  API:     http://localhost:8000/health"
echo "  Миниапп: http://localhost:8080  (HTTPS: https://localhost:8443)"
echo "  Сайт без Telegram: открой миниапп в браузере — гостевой режим включится сам"
