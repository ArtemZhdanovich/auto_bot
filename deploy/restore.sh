#!/usr/bin/env bash
# Восстановление из бэкапа: ./restore.sh /var/backups/autobot/autoselect_YYYYmmdd_HHMMSS.sql.gz
# ВНИМАНИЕ: перезапишет текущую БД. Сначала: docker compose stop bot worker_fetch
set -euo pipefail
cd "$(dirname "$0")/.."

FILE="${1:?использование: restore.sh <файл.sql.gz>}"
docker compose exec -T postgres psql -U app -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='autoselect' AND pid<>pg_backend_pid();"
docker compose exec -T postgres psql -U app -d autoselect -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
gunzip -c "$FILE" | docker compose exec -T postgres psql -U app -d autoselect
echo "restored from $FILE — теперь: docker compose up -d && docker compose exec backend alembic upgrade head"
