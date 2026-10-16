#!/usr/bin/env bash
# Ежедневный бэкап БД (cron: 0 3 * * * /opt/autobot/deploy/backup.sh).
# Держит последние 14 копий. Проверка восстановления — restore.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-/var/backups/autobot}"
KEEP=14
mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
FILE="$BACKUP_DIR/autoselect_$STAMP.sql.gz"

docker compose exec -T postgres pg_dump -U app -d autoselect | gzip -9 > "$FILE"
echo "backup written: $FILE ($(du -h "$FILE" | cut -f1))"

# Ротация: оставить последние KEEP
ls -1t "$BACKUP_DIR"/autoselect_*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm --
