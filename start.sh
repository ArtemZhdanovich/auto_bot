#!/usr/bin/env bash
# auto_bot — запуск стека (Linux/macOS; на VPS используй deploy/bootstrap.sh).
# Использование: ./start.sh [mono|micro]   (по умолчанию mono)
#
# Запускает ядро (без qdrant/minio — зеркало часто 403, монолиту не нужно),
# ждёт health, гонит миграции, печатает ссылки localhost и из LAN.
set -euo pipefail
cd "$(dirname "$0")"
PROFILE="${1:-mono}"
CORE=(postgres redis rabbitmq backend bot miniapp worker_fetch)

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[ -z "${LAN_IP:-}" ] && LAN_IP="$(ipconfig 2>/dev/null | grep -oE '10\.[0-9.]+|192\.168\.[0-9.]+' | head -1 || true)"

echo "[1/4] Инфраструктура..."
docker compose up -d "${CORE[@]}"

echo "[2/4] Ожидание backend + миграции..."
for _ in $(seq 1 30); do
  curl -fs http://localhost:8000/health >/dev/null 2>&1 && break
  sleep 2
done
curl -fs http://localhost:8000/health >/dev/null || { echo "backend не поднялся — docker compose logs backend"; exit 1; }
docker compose exec -T backend alembic upgrade head

echo "[3/4] Сборка/запуск профиля $PROFILE (если что-то не поднялось)..."
docker compose --profile "$PROFILE" up -d "${CORE[@]}"

echo "[4/4] Готово!"
echo
echo "  --- На этой машине ---"
echo "  API:     http://localhost:8000/health"
echo "  Сайт:    https://localhost:8443   (HTTP: http://localhost:8080)"
if [ -n "${LAN_IP:-}" ]; then
  echo
  echo "  --- Из локальной сети ---"
  echo "  Сайт:    https://${LAN_IP}:8443  (self-signed — «принять риск»)"
  echo "  API:     http://${LAN_IP}:8000/health"
fi
echo
echo "  Логи: docker compose logs -f backend bot"
