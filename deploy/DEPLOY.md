# Боевое развёртывание MVP (ранбук)

Целевая схема: **VPS (Ubuntu) + docker compose (профиль mono) + Caddy (авто-Let's Encrypt)**.

```text
Telegram ──web_app──▶ https://DOMAIN (Caddy, LE-серт) ──▶ miniapp nginx ──/api──▶ backend:8000
                                        │
Бот (long polling) ──backend API────────┘   postgres / redis / rabbitmq (не публичные)
```

## 0. Что нужно
- VPS: 2 vCPU / 4 GB RAM / 40 GB (Ubuntu 22.04+), порт 22 открыт.
- Домен: A-запись `DOMAIN → IP сервера` (например, duckdns — бесплатно).
- `BOT_TOKEN` от @BotFather.
- Ключи: `GLM_API_KEY` (или другой LLM).

## 1. Код на сервер
```bash
ssh root@SERVER
git clone <repo> /opt/autobot && cd /opt/autobot
```

## 2. Bootstrap (одна команда)
```bash
DOMAIN=auto.example.com ACME_EMAIL=you@mail.ru BOT_TOKEN=123456:ABC \
  bash deploy/bootstrap.sh
```
Скрипт: ставит Docker → генерирует `.env` с **боевыми** секретами (64-hex
APP_SECRET_KEY + отдельный INTERNAL_TOKEN + пароль БД) → поднимает стек →
миграции → cron бэкапа (03:00, 14 копий) → UFW (наружу только 22/80/443).

LLM-ключ вписать после: `nano .env` (GLM_API_KEY) →
`docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml --profile mono up -d backend bot`.

## 3. Проверка боеспособности
```bash
curl -s https://DOMAIN/health                 # ok
openssl s_client -connect DOMAIN:443 </dev/null 2>/dev/null | openssl x509 -noout -issuer   # Let's Encrypt
```
- Telegram: `/start` у бота → в меню **нативная web_app-кнопка** (LE-серт доверенный).
- Карточка машины → «🤖 Анализ цены» → вердикт + график в миниаппе.

## 4. Обновление версии
```bash
git pull
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml --profile mono up -d --build
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml exec backend alembic upgrade head
```

## 5. Бэкапы / восстановление
- Копии: `/var/backups/autobot/autoselect_*.sql.gz` (cron, 14 шт).
- Тест восстановления **раз в месяц**:
```bash
docker compose stop bot worker_fetch
bash deploy/restore.sh /var/backups/autobot/<файл>.sql.gz
docker compose start bot worker_fetch
```

## 6. Мониторинг
- `curl -fs https://DOMAIN/health` — внешняя проверка (UptimeRobot/cron на другой машине).
- Алерты уже идут в Telegram: `ADMIN_CHAT_ID` (падения источников, парсер, платежи — `app/services/alerting.py`).
- Логи с ротацией (10 MB × 3): `docker compose logs -f backend`.

## 7. Известные ограничения MVP
- Один хост, без репликации БД — ок для MVP, при росте → managed Postgres.
- `docker.1panel.live`-mirror может отдавать 403 на редкие образы (minio/qdrant):
  `docker pull <image>` вручную или временно убрать сервис из профиля.
- Бэкапы лежат локально — раз в неделю копировать офбокс: `scp`/rclone.

## 8. Секреты (политика)
- В `.env` на сервере; в git НЕ попадает (проверено: никогда не коммитился).
- Ротация `APP_SECRET_KEY` инвалидирует JWT — пользователи переподнимут сессию
  через initData автоматически.
- Аудит и журнал инцидентов: `INCIDENTS.md` (INC-001…005).

## 9. Доступ из локальной сети (стенд на своей машине)

Docker публикует порты на 0.0.0.0, правило брандмауэра «Docker Desktop Backend»
(Public, Any TCP) уже разрешает входящие — из LAN всё открывается само:

| Что | URL |
|---|---|
| Миниапп (сайт, гостевой режим) | `https://<LAN-IP>:8443` (браузер: «принять риск» — серт self-signed с SAN этого IP) |
| Миниапп по HTTP | `http://<LAN-IP>:8080` |
| API | `http://<LAN-IP>:8000/health` |

Проверка с другого устройства: `curl -k https://<LAN-IP>:8443/api/listings?limit=1`.

- IP узнать: `ipconfig` (IPv4 основного адаптера). В стенде сейчас `10.132.137.38`.
- **IP сменился (DHCP)** → обнови `LAN_IP` и `MINIAPP_URL=https://<новый-IP>:8443`
  в `.env`, затем `docker compose --profile mono up -d miniapp bot`
  (SAN self-signed-серта генерится из `NG_HOST_IP=${LAN_IP}`).
- Если брандмауэр режет (нет правила Docker), от адмира:
  `netsh advfirewall firewall add rule name="auto_bot miniapp 8443" dir=in action=allow protocol=TCP localport=8443 profile=any`
  (аналогично 8080/8000).
- Текущий профиль сети — **Public**: держи 8000 закрытым снаружи роутера, в интернет эти порты не пробрасывай (для этого — VPS по §1–3).

## 10. Тестовый стенд сайта (изолированный, параллельный)

Своя БД (fresh volume проекта `auto_bot_test`), порты 8001/8081/8444,
LLM = **mock** (разбор цены вернёт заглушку — токены GLM не тратятся),
без бота и воркеров. Демо-данные — копия listings+history из боевой БД.

```bash
bash deploy/teststand.sh        # поднять/обновить + залить демо-данные
bash deploy/teststand.sh nuke   # снести с данными
```

| Что | URL |
|---|---|
| Сайт (гостевой режим) | `https://localhost:8444` / из LAN `https://<LAN-IP>:8444` |
| API | `http://localhost:8001` |
| Тестовый Postgres | хост-порт `8002` |

Грабли (уже закрыты в `deploy/docker-compose.test.yml`):
- compose **мёржи** списки `ports` — оверрайд должен использовать `!override`,
  иначе тестовые redis/rabbitmq сядут на порты боевого (6379/5672/15672);
- тестовые `redis`/`rabbitmq` нужны backend-у по depends_on — поднимать командой
  целиком (postgres redis backend miniapp), а не только backend.
