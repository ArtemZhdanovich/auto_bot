#!/usr/bin/env bash
# ============================================================================
# MVP-стенд: сайт (backend + Mini App) + бот + LLM — минимум для демонстрации.
#
# Поднимает:  postgres, redis, rabbitmq, backend (:8000), bot, miniapp (:8080)
# НЕ поднимает: воркеры парсинга/рассылки, RAG, translate, MinIO (не нужны
#               для MVP: диалог работает через GLM напрямую, лента — из БД).
#
# Использование:
#   bash scripts/mvp.sh up           # поднять стенд + миграции + проверки
#   bash scripts/mvp.sh up --parse   # + fetch-воркер и 1 страница encar (данные в ленте)
#   bash scripts/mvp.sh down         # остановить стенд
#   bash scripts/mvp.sh logs         # хвост логов backend/bot/miniapp
#   bash scripts/mvp.sh urls         # показать адреса
#   bash scripts/mvp.sh whoami       # кто уже писал боту (telegram_id из БД)
#   bash scripts/mvp.sh admin <id>   # сделать юзера супер-админом демо:
#                                    #   ADMIN_CHAT_ID в .env + users.role='admin'
#                                    #   + тариф business на 30 дней; рестарт бота
#
# Требования в .env (см. .env.example):
#   BOT_TOKEN      — токен бота (без него бот не стартует)
#   APP_SECRET_KEY — любой секрет
#   GLM_API_KEY    — ключ LLM. НЕТ КЛЮЧА? Стенд поднимется в mock-режиме:
#                    добавьте в .env  LLM_PROVIDER=mock  (заготовленные ответы,
#                    диалог/сайт работают, зовут LLM не реальный).
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
# Профиль mono: mvp.sh работает с монолитом (backend/bot/workers/miniapp).
# Режим микросервисов: docker compose --profile micro up --build (см. README).
COMPOSE="docker compose --profile mono"
BASE_URL="${APP_BASE_URL:-http://localhost:8000}"
MINIAPP_URL="${MINIAPP_URL:-http://localhost:8080}"
CORE="postgres redis rabbitmq"
MVP="backend bot miniapp"
ADMIN_ID="809464979"   # хардкод: супер-админ демо (fallback, если нет в .env)

log()  { printf '\033[1;36m[mvp]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[mvp]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[mvp] ОШИБКА:\033[0m %s\n' "$*" >&2; exit 1; }

# --- проверки окружения -----------------------------------------------------

[ -f .env ] || die "нет .env — скопируйте: cp .env.example .env"

env_has() { grep -Eq "^$1=..*" .env; }   # переменная задана непустым значением

env_has BOT_TOKEN      || warn "BOT_TOKEN пуст — бот не сможет подключиться к Telegram"
env_has APP_SECRET_KEY || warn "APP_SECRET_KEY не задан — будет dev-значение (только для локальной демки)"
if env_has GLM_API_KEY; then
  log "GLM_API_KEY найден — LLM будет реальный"
elif grep -Eq "^LLM_PROVIDER=mock" .env; then
  warn "GLM_API_KEY отсутствует, но LLM_PROVIDER=mock — диалог отвечают заготовки"
else
  warn "GLM_API_KEY не задан и LLM_PROVIDER не mock — диалог будет падать."
  warn "  Варианты: 1) вставьте GLM_API_KEY в .env"
  warn "            2) добавьте в .env строку  LLM_PROVIDER=mock  (демка без LLM)"
fi

wait_http() { # $1=url  $2=имя
  for _ in $(seq 1 30); do
    if curl -sf -o /dev/null "$1"; then log "$2 — OK"; return 0; fi
    sleep 2
  done
  die "$2 не отвечает: $1 (логи: $COMPOSE logs backend)"
}

wait_pg() {
  for _ in $(seq 1 30); do
    if $COMPOSE exec -T postgres pg_isready -U app -d autoselect >/dev/null 2>&1; then
      log "postgres — OK"; return 0
    fi
    sleep 2
  done
  die "postgres не поднялся (логи: $COMPOSE logs postgres)"
}

cmd_up() {
  log "1/5 Инфраструктура: $CORE"
  $COMPOSE up -d $CORE
  wait_pg

  log "2/5 Миграции (alembic upgrade head)"
  $COMPOSE run --rm --no-deps backend alembic upgrade head

  log "3/5 Приложение: $MVP"
  $COMPOSE up -d $MVP
  wait_http "$BASE_URL/health" "backend"

  # /health/detailed: 200 = БД жива; redis/rabbit показываем как есть
  if curl -sf "$BASE_URL/health/detailed" | grep -q '"rabbitmq": false'; then
    warn "rabbitmq ещё поднимается — очередь задач заработает через минуту"
  fi

  if [ "${1:-}" = "--parse" ]; then
    log "3b. Fetch-воркер + данные для ленты"
    $COMPOSE up -d worker_fetch
    TOKEN=$(grep -E '^INTERNAL_TOKEN=..*' .env | head -1 | cut -d= -f2- || true)
    [ -n "$TOKEN" ] || TOKEN=$(grep -E '^APP_SECRET_KEY=' .env | head -1 | cut -d= -f2-)
    # источник создаётся/обновляется через админ-API (сидирования в миграциях нет)
    curl -sf -X PUT "$BASE_URL/api/admin/sources/encar" \
      -H "X-Internal-Token: $TOKEN" -H "Content-Type: application/json" \
      -d '{"name":"encar","adapter_class":"EncarApiAdapter","kind":"api",
           "enabled":true,"priority":100,
           "capabilities":{"country":"KR","price_currency":"KRW"},
           "config":{"page_size":20}}' >/dev/null \
      || warn "не удалось создать источник encar (проверьте INTERNAL_TOKEN)"
    curl -sf -X POST "$BASE_URL/api/admin/sources/encar/fetch?max_pages=1" \
      -H "X-Internal-Token: $TOKEN" >/dev/null \
      || warn "fetch не запустился (нужна сеть до encar.com; без прокси может 403)"
    warn "парсинг идёт в фоне (worker_fetch); лента заполнится через ~1-2 мин"
  fi

  log "4/5 Готово. Адреса:"
  cmd_urls
  log "5/5 Откройте бота в Telegram и напишите ему — работает AI-диалог (GLM)."
  log "Логи: bash scripts/mvp.sh logs   Остановить: bash scripts/mvp.sh down"
}

cmd_down() {
  log "Останавливаю MVP-стенд (инфраструктура остаётся):"
  $COMPOSE stop backend bot miniapp worker_fetch 2>/dev/null || true
  log "Полный стоп с инфраструктурой: docker compose down"
}

cmd_logs() { $COMPOSE logs -f --tail=50 backend bot miniapp; }

cmd_urls() {
  echo "  • API (Swagger): $BASE_URL/docs"
  echo "  • Health:        $BASE_URL/health/detailed"
  echo "  • Mini App:      $MINIAPP_URL  (вне Telegram — read-only лента;"
  echo "                   полноценный режим — открыть через кнопку бота)"
}

psql() { $COMPOSE exec -T postgres psql -U app -d autoselect "$@"; }

cmd_whoami() {
  log "Последние юзеры, писавшие боту (напишите боту /start, если список пуст):"
  psql -c "SELECT telegram_id, COALESCE(username,'-') AS username,
                  COALESCE(first_name,'-') AS name, role, subscription_tier
           FROM users ORDER BY created_at DESC LIMIT 10;"
  log "Назначить супер-админа:  bash scripts/mvp.sh admin <telegram_id>"
}

cmd_admin() {
  local id="${1:-$ADMIN_ID}"
  case "$id" in ''|*[!0-9]*) die "укажите числовой telegram_id: bash scripts/mvp.sh admin 123456789" ;; esac
  log "Назначаю супер-админа: $id"

  # 1. ADMIN_CHAT_ID в .env (алерты/уведомления админу идут ему)
  if grep -qE '^ADMIN_CHAT_ID=' .env; then
    sed -i "s/^ADMIN_CHAT_ID=.*/ADMIN_CHAT_ID=$id/" .env
  else
    printf '\nADMIN_CHAT_ID=%s\n' "$id" >> .env
  fi

  # 2. роль + демо-тариф business (30 дней: все фильтры, instant, AI без лимита)
  psql -c "UPDATE users SET role='admin',
                  subscription_tier='business',
                  subscription_until = now() + interval '30 days'
            WHERE telegram_id = $id
            RETURNING telegram_id, username, role, subscription_tier, subscription_until;"

  # 3. рестарт бота, чтобы он увидел новый ADMIN_CHAT_ID
  $COMPOSE up -d --force-recreate bot >/dev/null
  log "Юзер $id — супер-админ: ADMIN_CHAT_ID + role=admin + business/30д. Бот перезапущен."
  warn "Демо-тариф снимается: UPDATE users SET subscription_tier='free', subscription_until=NULL WHERE telegram_id=$id;"
}

case "${1:-up}" in
  up)     shift || true; cmd_up "$@" ;;
  down)   cmd_down ;;
  logs)   cmd_logs ;;
  urls)   cmd_urls ;;
  whoami) cmd_whoami ;;
  admin)  shift || true; cmd_admin "${1:-}" ;;
  *) die "использование: bash scripts/mvp.sh {up [--parse]|down|logs|urls|whoami|admin <id>}" ;;
esac
