"""Конфигурация приложения (ТЗ п. 11.6 — переменные окружения)."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Дефолтный dev-секрет. Вынесен в константу, чтобы prod-проверка не зависела
# от того, как pydantic хранит default в model_fields (и не падала на SecretStr).
_DEV_SECRET_DEFAULT = "dev-secret-change-me-0123456789abcdef0123456789abcdef"


def _has_secret(value: SecretStr | None) -> bool:
    """Проверяет, что `SecretStr` задан и непустой.

    Args:
        value: Значение секрета или `None`.

    Returns:
        `True`, если секрет присутствует и его содержимое непустое.
    """
    return bool(value and value.get_secret_value())


class Settings(BaseSettings):
    """Настройки приложения из env / `.env` (ТЗ п. 11.6).

    Секреты хранятся в `SecretStr`: они не попадают в `repr()`, `model_dump()`,
    логи и Sentry breadcrumbs. В проде включён fail-fast на dev-дефолт,
    короткие ключи и пустой `INTERNAL_TOKEN` (INC-001, INC-004).

    Attributes:
        app_env: Окружение: dev | test | staging | prod.
        app_secret_key: Секрет для подписи JWT (и fallback для internal API в dev).
        internal_token: Отдельный секрет для internal/admin API (X-Internal-Token).
        bot_token: Токен Telegram-бота.
        jwt_algorithm: Алгоритм подписи JWT (HS256/384/512; `none` запрещён).
        llm_provider: Провайдер LLM: glm | anthropic | ollama | mock.
        log_level: Уровень логирования.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: Literal["dev", "test", "staging", "prod"] = "dev"
    app_secret_key: SecretStr = SecretStr(_DEV_SECRET_DEFAULT)
    # Отдельный секрет для внутренних эндпоинтов (X-Internal-Token).
    # Пустое значение = fallback на APP_SECRET_KEY (только для dev/test;
    # в prod требуется задать явно — см. _prod_secrets_required).
    internal_token: SecretStr = SecretStr("")
    app_base_url: str = "http://localhost:8000"
    miniapp_url: str = "http://localhost:8080"

    # Bot
    bot_token: SecretStr = SecretStr("")
    # Юзернейм бота (без @) для ссылок «Войти через Telegram» с сайта;
    # пусто = веб-вход через Telegram недоступен (кнопка вернёт 503).
    bot_username: str = ""
    admin_chat_id: int | None = None
    manager_chat_id: int | None = None

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalize_app_env(cls, v: object) -> object:
        """Нормализует окружение: strip, lower, алиас `production` → `prod`.

        Без нормализации `APP_ENV=PROD` / `APP_ENV="prod "` обходили бы
        prod-проверки в `_prod_secrets_required` (fail-open).

        Args:
            v: Сырое значение из env/`.env`.

        Returns:
            Нормализованная строка окружения либо исходное значение.
        """
        if isinstance(v, str):
            v = v.strip().lower()
            if v == "production":
                return "prod"
        return v

    @field_validator("bot_username", mode="before")
    @classmethod
    def _strip_bot_username_at(cls, v: object) -> object:
        """Убирает ведущий `@` из BOT_USERNAME (в ссылках @ не нужен).

        Args:
            v: Сырое значение из env/`.env`.

        Returns:
            Строка без ведущего `@` или исходное значение.
        """
        if isinstance(v, str):
            return v.lstrip("@")
        return v

    @field_validator("admin_chat_id", "manager_chat_id", mode="before")
    @classmethod
    def _empty_chat_id_to_none(cls, v: object) -> object:
        """Пустая строка в .env (ADMIN_CHAT_ID=) означает «не задан».

        Args:
            v: Сырое значение из env/`.env`.

        Returns:
            `None` для пустой строки, иначе исходное значение.
        """
        if v == "":
            return None
        return v

    @model_validator(mode="after")
    def _prod_secrets_required(self) -> "Settings":
        """Fail-fast (INC-004): в проде запрет дев-секрета и коротких ключей.

        Иначе сервис молча стартует с известным всему миру ключом, которым
        подписаны JWT и (по fallback) закрыт internal API.

        Returns:
            Валидированный объект настроек.

        Raises:
            ValueError: Если в prod не задан / слишком короткий `APP_SECRET_KEY`
                или пустой `INTERNAL_TOKEN`.
        """
        if self.app_env == "prod":
            secret = self.app_secret_key.get_secret_value()
            if secret == _DEV_SECRET_DEFAULT:
                raise ValueError(
                    "APP_SECRET_KEY не задан (используется dev-дефолт) — в prod это запрещено"
                )
            if len(secret) < 32:
                raise ValueError(
                    "APP_SECRET_KEY короче 32 символов — в prod это запрещено (INC-001)"
                )
            # Fallback на APP_SECRET_KEY в prod недопустим: компрометация
            # JWT-секрета иначе автоматически открывает internal API.
            if not _has_secret(self.internal_token):
                raise ValueError(
                    "INTERNAL_TOKEN обязателен в prod: fallback на APP_SECRET_KEY "
                    "связывает компрометацию JWT-секрета с доступом к internal API"
                )
            # Чаты (05 §4): дев-секрет centrifugo в prod позволяет фальшивый
            # connection-JWT (sub=user:{любой id}) → подписка на чужой личный
            # канал user:{id} уведомлений; api-ключ — публикацию в каналы.
            if self.centrifugo_secret.get_secret_value() == "change-me":
                raise ValueError(
                    "CENTRIFUGO_SECRET не задан (dev-дефолт change-me) — в prod "
                    "это запрещено: подделка ws-JWT открывает чужие каналы"
                )
            if self.centrifugo_http_api_key.get_secret_value() == "change-me":
                raise ValueError(
                    "CENTRIFUGO_HTTP_API_KEY не задан (dev-дефолт change-me) — "
                    "в prod это запрещено: дев-ключ публикует в каналы чатов"
                )
            # TECHDEBT 1.6: дев-дефолты URL (localhost/guest-креды) в prod —
            # верный способ «случайно» поднять прод на локальной БД или
            # RabbitMQ с guest/guest.
            if self.database_url == self.model_fields["database_url"].default:
                raise ValueError(
                    "DATABASE_URL не задан (dev-дефолт localhost) — в prod это запрещено"
                )
            if self.rabbitmq_url == self.model_fields["rabbitmq_url"].default or (
                "guest:guest" in self.rabbitmq_url
            ):
                raise ValueError(
                    "RABBITMQ_URL не задан или использует guest:guest — в prod это запрещено"
                )
            if self.redis_url == self.model_fields["redis_url"].default:
                raise ValueError(
                    "REDIS_URL не задан (dev-дефолт localhost) — в prod это запрещено"
                )
        return self

    # DB
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/autoselect"

    # Redis (данные: дедуп, rate limit, кэш, DialogState)
    redis_url: str = "redis://localhost:6379/0"

    # RabbitMQ (очереди задач, миграция с ARQ/Redis)
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    # Auth
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_sec: int = Field(default=3600, gt=0)
    refresh_token_ttl_sec: int = Field(default=60 * 60 * 24 * 30, gt=0)
    init_data_max_age_sec: int = Field(default=60 * 60 * 24, gt=0, le=60 * 60 * 24 * 30)

    @model_validator(mode="after")
    def _check_token_ttls(self) -> "Settings":
        """Проверяет, что refresh-токен живёт дольше access-токена.

        Returns:
            Валидированный объект настроек.

        Raises:
            ValueError: Если `REFRESH_TOKEN_TTL_SEC <= ACCESS_TOKEN_TTL_SEC`.
        """
        if self.refresh_token_ttl_sec <= self.access_token_ttl_sec:
            raise ValueError(
                "REFRESH_TOKEN_TTL_SEC должен быть больше ACCESS_TOKEN_TTL_SEC"
            )
        return self

    # Партнёрский API (P2-10): rate limit запросов на партнёрский ключ в минуту
    partner_rate_limit_per_min: int = Field(default=60, gt=0)

    # LLM (этап 3): основной провайдер — GLM (Zhipu); OpenAI выпилен
    glm_api_key: SecretStr | None = None
    # Международный эндпоинт z.ai; для ключей open.bigmodel.cn задайте
    # GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
    glm_base_url: str = "https://api.z.ai/api/paas/v4"
    anthropic_api_key: SecretStr | None = None
    llm_default_model: str = "glm-4.6"
    llm_advanced_model: str = "glm-4.6"
    # glm | anthropic | ollama | mock ("mock" — для тестов/dev без LLM)
    llm_provider: Literal["glm", "anthropic", "ollama", "mock"] = "glm"
    llm_base_url: str | None = None  # переопределение (например, Ollama/прокси)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def _check_llm_keys(self) -> "Settings":
        """Требует ключ провайдера LLM, если он выбран.

        В dev/test допускается отсутствие ключа (провайдер `mock`), в prod —
        ошибка старта, чтобы не упасть в рантайме на первом запросе.

        Returns:
            Валидированный объект настроек.

        Raises:
            ValueError: В prod при `llm_provider=glm|anthropic` без ключа.
        """
        if not self.is_prod:
            return self
        if self.llm_provider == "glm" and not _has_secret(self.glm_api_key):
            raise ValueError("GLM_API_KEY обязателен при LLM_PROVIDER=glm (prod)")
        if self.llm_provider == "anthropic" and not _has_secret(self.anthropic_api_key):
            raise ValueError(
                "ANTHROPIC_API_KEY обязателен при LLM_PROVIDER=anthropic (prod)"
            )
        return self

    # AI-диалог (ТЗ п. 7.6, 7.7)
    dialog_ttl_sec: int = Field(default=60 * 60 * 24, gt=0)  # 24 часа
    dialog_history_len: int = Field(default=8, gt=0)  # последние N сообщений в контексте
    faq_cache_ttl_sec: int = Field(default=86400, ge=0)

    # RAG-сервис (план внедрения RAG, фазы 3-4; внешний rag_service)
    # Пустой URL = RAG выключен → answer_question отвечает через LLM (fallback).
    rag_service_url: str | None = None  # напр. http://rag.internal:8100
    rag_api_token: SecretStr | None = None  # Bearer-токен для RAG Service
    rag_enabled: bool = True  # feature flag (rollout 10% → 100%)
    rag_timeout_sec: int = Field(default=5, gt=0)
    rag_cache_ttl_sec: int = Field(default=3600, ge=0)  # кэш ответов в Redis, TTL 1 час
    rag_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)  # ниже — fallback на LLM
    rag_cb_fail_threshold: int = Field(default=5, gt=0)  # circuit breaker: N ошибок подряд
    rag_cb_cooldown_sec: int = Field(default=300, ge=0)  # пауза после открытия, 5 мин

    # Уведомления (этап 4, ТЗ п. 4.6, 8.6)
    notify_rate_limit_sec: int = Field(default=30, ge=0)  # не чаще 1 сообщения / 30 сек (п. 8.6)
    # Биллинг партнёров (P2-14): CPL-тарифы по планам, USD за лид.
    # Индивидуальный cpl_rate партнёра перекрывает план.
    partner_cpl_rates: dict[str, float] = Field(
        default_factory=lambda: {"starter": 5.0, "pro": 3.0, "salon": 2.0}
    )
    # Token Bucket Telegram (ТЗ п. 12.6): ~30 msg/s глобально, ~1 msg/s на чат.
    # 0 = лимитер выключен; Redis лежит → fail-open (429-обработка — страховка).
    telegram_rate_global_per_sec: float = Field(default=30.0, ge=0)
    telegram_rate_chat_per_sec: float = Field(default=1.0, ge=0)
    notify_scan_window_sec: int = Field(default=1800, gt=0)  # «свежие» объявления для сканирования фильтров
    notify_sent_ttl_sec: int = Field(default=60 * 60 * 24 * 7, ge=0)  # notified:{uid}:{lid}, 7 дней
    digest_max_cards: int = Field(default=5, ge=1, le=10)  # 3–5 карточек в дайджесте
    notify_quiet_hours_default: str | None = None  # глобальные тихие часы "23:00-08:00"

    @field_validator("partner_cpl_rates")
    @classmethod
    def _check_cpl_rates(cls, v: dict[str, float]) -> dict[str, float]:
        """Проверяет, что все CPL-тарифы положительные.

        Args:
            v: Словарь `{plan: rate}`.

        Returns:
            Исходный словарь, если все значения > 0.

        Raises:
            ValueError: Если хотя бы один тариф <= 0.
        """
        if any(rate <= 0 for rate in v.values()):
            raise ValueError("Все значения PARTNER_CPL_RATES должны быть > 0")
        return v

    # Отслеживание объявлений (миграция 0009, stale-cron фазы 3)
    stale_max_age_days: int = Field(default=3, gt=0)  # не виден N дней → is_active=false (CB-источники пропускаются)

    # Перевод тайтлов (PLAN.md, фаза 5; сервис services/translate).
    # Перевод некритичен: выключен флагом или пустым URL → показывается оригинал.
    translate_enabled: bool = False  # feature flag
    translate_service_url: str = ""  # напр. http://translate:8200
    translate_api_token: SecretStr | None = None  # Bearer; = API_TOKEN сервиса
    translate_timeout_sec: float = Field(default=180.0, gt=0)  # NLLB на CPU: инференс сериализован локом,
    # чанк может ждать своей очереди за парой параллельных HTTP-запросов
    translate_batch_size: int = Field(default=32, gt=0)
    translate_cache_ttl_sec: int = Field(default=60 * 60 * 24 * 30, ge=0)  # tr:{tgt}:{src}:{sha1}, 30 дней
    translate_cb_fail_threshold: int = Field(default=3, gt=0)  # circuit breaker: N ошибок подряд
    translate_cb_cooldown_sec: int = Field(default=300, ge=0)
    translate_target_lang: str = "rus_Cyrl"  # NLLB-код целевого языка
    # NLLB-код исходного языка по источнику; "auto" — детект по письменности в сервисе
    translate_source_langs: dict[str, str] = {
        "encar": "kor_Hang",
        "kcar": "kor_Hang",
        "che168": "zho_Hans",
    }

    # Архитектурный rollout (фаза 3): toggle избранного через use case.
    # False — старый код роутера, True — Clean Architecture-срез.
    use_case_favorites: bool = True
    # S3/MinIO: raw_payload в холодное хранилище (PLAN.md, фаза 6).
    # aioboto3 (не блокирует event loop воркеров); MinIO — через endpoint_url.
    # Выключено (флаг/пустой endpoint) → raw не пишется никуда, объявления
    # сохраняются как обычно (fallback обязателен).
    raw_payload_enabled: bool = False  # feature flag
    s3_endpoint_url: str = ""  # напр. http://localhost:9000 (пусто = выключен)
    s3_bucket: str = "raw-payloads"
    s3_access_key: str = ""
    s3_secret_key: SecretStr = SecretStr("")
    s3_region: str = "us-east-1"
    raw_retention_days: int = Field(default=180, gt=0)  # retention-крон: удалять объекты старше N дней
    raw_presign_ttl_sec: int = Field(default=600, gt=0)  # presigned GET для админки, 10 мин
    # Фотографии лотов рынка (07 §1): отдельный bucket с public-read
    # policy — фронт грузит <img> напрямую, без presign. Креды/endpoint
    # общие с raw-payload (S3_*); публичный адрес собирается из
    # S3_PUBLIC_URL (MinIO из браузера виден по localhost, не по minio).
    s3_photos_bucket: str = "market-photos"
    s3_public_url: str = "http://localhost:9000"
    # Зеркалирование фото внешних лотов при парсинге (docs/REDESIGN/11 §3):
    # первые N фото лота качаются в S3; 0 = выключено.
    market_photo_mirror_limit: int = 5
    # Лотов за один прогон фоновой задачи mirror_photos_task
    market_mirror_batch: int = 40

    @model_validator(mode="after")
    def _check_s3_config(self) -> "Settings":
        """Требует endpoint и креды S3, если включён `RAW_PAYLOAD_ENABLED`.

        Returns:
            Валидированный объект настроек.

        Raises:
            ValueError: При включённом флаге и пустом endpoint/кредах.
        """
        if not self.raw_payload_enabled:
            return self
        if not self.s3_endpoint_url:
            raise ValueError("S3_ENDPOINT_URL обязателен при RAW_PAYLOAD_ENABLED=true")
        if not self.s3_access_key or not _has_secret(self.s3_secret_key):
            raise ValueError(
                "S3_ACCESS_KEY и S3_SECRET_KEY обязательны при RAW_PAYLOAD_ENABLED=true"
            )
        return self

    # Прокси DataImpulse (опционально): шлюз для парсеров, если источники глушат.
    # Официальный формат: http://login__cr.cn;sessid.X:password@gw.dataimpulse.com:823
    # Задаёте user+pass → источники с capabilities.use_proxy=true ходят через шлюз.
    proxy_dataimpulse_user: str | None = None
    proxy_dataimpulse_pass: SecretStr | None = None
    proxy_dataimpulse_host: str = "gw.dataimpulse.com"
    proxy_dataimpulse_port: int = Field(default=823, gt=0, le=65535)  # 823 = HTTPS, 824 = SOCKS5
    proxy_dataimpulse_geo: str | None = None  # cr.*: "cn" / "cn,de" (None = весь мир)
    # 0 = ротация IP на каждый запрос (default у провайдера); иначе sticky-окно
    proxy_dataimpulse_sticky_ttl_sec: int = Field(default=1800, ge=0)
    proxy_dataimpulse_fail_threshold: int = Field(default=5, gt=0)  # ConnectError подряд → пауза шлюза
    proxy_dataimpulse_fail_cooldown_sec: int = Field(default=300, ge=0)  # пауза: ходим напрямую

    @model_validator(mode="after")
    def _check_proxy_config(self) -> "Settings":
        """Проверяет, что user/pass прокси заданы парой.

        Returns:
            Валидированный объект настроек.

        Raises:
            ValueError: Если задан только один из пары user/pass.
        """
        has_user = bool(self.proxy_dataimpulse_user)
        has_pass = _has_secret(self.proxy_dataimpulse_pass)
        if has_user != has_pass:
            raise ValueError(
                "PROXY_DATAIMPULSE_USER и PROXY_DATAIMPULSE_PASS задаются парой"
            )
        return self

    # --- Микросервисы (профиль micro; gateway маршрутизирует /api/*) ---
    # 150с: POST /api/listings/{id}/analysis гоняет LLM до 120с
    # (app/transport/bot/client.py — тот же лимит) — дефолт 15с рвал
    # долгие разборы: «анализ не всегда в тайминги попадает».
    gateway_timeout_sec: float = Field(default=150.0, gt=0)
    svc_identity_url: str = "http://localhost:8101"
    svc_catalog_url: str = "http://localhost:8102"
    svc_billing_url: str = "http://localhost:8103"
    svc_partners_url: str = "http://localhost:8104"
    svc_dialog_url: str = "http://localhost:8105"
    svc_admin_url: str = "http://localhost:8106"
    svc_chat_url: str = "http://localhost:8107"

    # --- Centrifugo (чаты/внутренний рынок, редизайн часть 5) ---
    # Сервер websocket-пушей: publish-API для бэкенда и точка подключения
    # браузерных клиентов (JWT подписывается centrifugo_secret, публикация —
    # по centrifugo_http_api_key). Dev-дефолты — локальный centrifugo.
    centrifugo_url: str = "http://localhost:8008"
    # URL для БРАУЗЕРА (ws-endpoint centrifugo): в docker внутренний
    # centrifugo:8008 с хоста недостижим — публичный отдаётся в ws-token.
    centrifugo_public_url: str = ""
    centrifugo_secret: SecretStr = SecretStr("change-me")
    centrifugo_http_api_key: SecretStr = SecretStr("change-me")

    # Guest rate limit (TECHDEBT 1.4): созданий гостевых сессий с одного IP
    # в минуту (фиксированное окно в Redis; превышение → 429).
    guest_ip_rate_limit: int = Field(default=5, gt=0)

    # --- Рынок объявлений пользователей (редизайн часть 6, §3) ---
    # Бесплатный период размещения лота юзера (дней) и цена недели
    # продления в Stars (1 нед = market_week_stars, 4 нед = x3).
    market_free_days: int = Field(default=7, gt=0)
    market_week_stars: int = Field(default=20, gt=0)
    # Сколько платных (по Stars) лотов сверх бесплатного/включённого.
    market_user_max_paid_lots: int = Field(default=3, ge=1)
    # Кулдаун бесплатного периода (дней): free-период даётся не чаще раза
    # в N дней на юзера — иначе ротация «снять/создать» даёт бесконечные
    # бесплатные недели (аудит 06 §3). 0 — отключить кулдаун.
    market_free_cooldown_days: int = Field(default=30, ge=0)
    # Пакеты размещений для партнёров: {"размер": цена_в_рублях}.
    market_partner_packages: dict[str, int] = Field(
        default_factory=lambda: {"10": 500, "50": 1800, "200": 5000}
    )
    # Словарь запрещённых слов авто-модерации лотов (10 §2: конкуренты,
    # крипта, оружие, «инвестиции», обман-паттерны; RU+EN). Сравнение —
    # подстрока без учёта регистра по описанию и марке/модели.
    market_forbidden_words: list[str] = Field(
        default_factory=lambda: [
            "авито",
            "avito",
            "drom",
            "авто.ру",
            "auto.ru",
            "крипта",
            "криптовалюта",
            "биткоин",
            "bitcoin",
            "оружие",
            "казино",
            "casino",
            "инвестиции",
            "заработок",
            "обман",
            "скам",
            "scam",
        ]
    )

    @property
    def market_packages(self) -> dict[int, Decimal]:
        """Пакеты партнёрских размещений: `{size: price_rub}` (Decimal).

        Ключи конфига — строки (env/JSON), бизнес-логике нужны int-размеры
        и точные суммы (баланс партнёра — Numeric(12,2)).
        """
        return {
            int(size): Decimal(str(price))
            for size, price in self.market_partner_packages.items()
        }

    # Alerting / мониторинг (этап 8, ТЗ п. 10)

    # Alerting / мониторинг (этап 8, ТЗ п. 10)
    alerts_enabled: bool = True  # алерты в Telegram на ADMIN_CHAT_ID
    alert_dedup_ttl_sec: int = Field(default=1800, ge=0)  # базовое подавление повторов alert:{key}
    dlq_alert_threshold: int = Field(default=10, gt=0)  # глубина dead_letter для алерта
    health_check_interval_sec: int = Field(default=300, gt=0)  # период мониторинга DLQ в воркерах
    source_cb_error_streak: int = Field(default=5, gt=0)  # ошибок подряд до открытия CB источника

    # Monitoring
    sentry_dsn: str | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: object) -> object:
        """Приводит LOG_LEVEL к верхнему регистру.

        Args:
            v: Сырое значение из env/`.env`.

        Returns:
            Строка в верхнем регистре либо исходное значение.
        """
        if isinstance(v, str):
            return v.strip().upper()
        return v

    @property
    def is_prod(self) -> bool:
        """`True`, если приложение запущено в prod-окружении.

        Returns:
            `True` при `app_env == "prod"`, иначе `False`.
        """
        return self.app_env == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Возвращает singleton-настройки приложения.

    Кэш на 1 элемент: конфиг читается один раз за процесс; в тестах
    сбрасывается через `get_settings.cache_clear()`.

    Returns:
        Экземпляр `Settings`, валидированный при первом вызове.
    """
    return Settings()