#!/usr/bin/env bash
# Боевой bootstrap VPS (Ubuntu 22/24). Запуск один раз от root:
#   DOMAIN=auto.example.com ACME_EMAIL=you@mail.ru BOT_TOKEN=123:ABC bash deploy/bootstrap.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DOMAIN="${DOMAIN:?задай DOMAIN=auto.example.com}"
ACME_EMAIL="${ACME_EMAIL:?задай ACME_EMAIL=you@mail.ru}"
BOT_TOKEN="${BOT_TOKEN:?задай BOT_TOKEN=123456:ABC...}"

# 1. Docker + compose plugin
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

# 2. .env — генерация с боевыми секретами (существующий не трогаем)
if [ ! -f .env ]; then
  cp .env.example .env
  APP_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
  INTERNAL_TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
  DB_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
  sed -i "s|^APP_ENV=.*|APP_ENV=prod|; s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=$APP_SECRET_KEY|; \
         s|^INTERNAL_TOKEN=.*|INTERNAL_TOKEN=$INTERNAL_TOKEN|; s|^DB_PASSWORD=.*|DB_PASSWORD=$DB_PASSWORD|; \
         s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://app:$DB_PASSWORD@localhost:5432/autoselect|; \
         s|^BOT_TOKEN=.*|BOT_TOKEN=$BOT_TOKEN|; \
         s|^MINIAPP_URL=.*|MINIAPP_URL=https://$DOMAIN|; \
         s|^APP_BASE_URL=.*|APP_BASE_URL=https://$DOMAIN|" .env
  echo ".env сгенерирован с боевыми секретами"
else
  echo ".env уже существует — НЕ трогаю (секреты внутри)"
fi

# 3. Файрвол: наружу только SSH/web
if command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null 2>&1 || true
  ufw allow 80,443/tcp >/dev/null 2>&1 || true
  yes | ufw enable >/dev/null 2>&1 || true
fi

# 4. Подъём (образы собираются на сервере; docker.1panel.live-mirror может 403 —
#    тогда: docker pull minio/minio:latest вручную или удалить сервис из профиля)
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml \
  --profile mono up -d --build

# 5. Миграции
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml \
  exec backend alembic upgrade head

# 6. Ежедневный бэкап в 03:00
( crontab -l 2>/dev/null | grep -v backup.sh ; \
  echo "0 3 * * * /bin/bash $(pwd)/deploy/backup.sh >> /var/log/autobot_backup.log 2>&1" ) | crontab -

echo ""
echo "=== Готово. Проверка:"
echo "  curl https://$DOMAIN/health"
echo "  https://$DOMAIN — Mini App (валидный LE-сертификат)"
echo "  Telegram: /start у бота → нативная web_app-кнопка"
