#!/bin/sh
# Генерация самоподписанного сертификата при первом старте (MVP, LAN).
# Для Telegram web_app нужен доверенный CA (Let's Encrypt) — см. PROGRESS.md.
set -e
mkdir -p /etc/nginx/certs
if [ ! -f /etc/nginx/certs/self.crt ]; then
  IP="${NG_HOST_IP:-127.0.0.1}"
  echo "entrypoint-ssl: generating self-signed cert for IP=${IP}"
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout /etc/nginx/certs/self.key -out /etc/nginx/certs/self.crt \
    -subj "/CN=auto-selection.local" \
    -addext "subjectAltName=DNS:localhost,DNS:auto-selection.local,IP:${IP},IP:127.0.0.1"
fi
exec "$@"
