"""Конфигурация приложения (ТЗ п. 11.6 — переменные окружения)."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_env: str = "dev"
    app_secret_key: str = "dev-secret-change-me-0123456789abcdef0123456789abcdef"
    # Отдельный секрет для внутренних эндпоинтов (X-Internal-Token);
    # пустое значение = обратная совместимость (используется APP_SECRET_KEY).
    internal_token: str = ""
    app_base_url: str = "http://localhost:8000"
    miniapp_url: str = "http://localhost:8080"

    # Bot
    bot_token: str = ""
    admin_chat_id: int | None = None
    manager_chat_id: int | None = None

    @field_validator("admin_chat_id", "manager_chat_id", mode="before")
    @classmethod
    def _empty_chat_id_to_none(cls, v: object) -> object:
        """Пустая строка в .env (ADMIN_CHAT_ID=) означает «не задан»."""
        if v == "":
            return None
        return v

    # DB
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/autoselect"

    # Redis (данные: дедуп, rate limit, кэш, DialogState)
    redis_url: str = "redis://localhost:6379/0"

    # RabbitMQ (очереди задач, миграция с ARQ/Redis)
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    # Auth
    jwt_algorithm: str = "HS256"
    access_token_ttl_sec: int = 3600
    refresh_token_ttl_sec: int = 60 * 60 * 24 * 30
    init_data_max_age_sec: int = 60 * 60 * 24

    # Партнёрский API (P2-10): rate limit запросов на партнёрский ключ в минуту
    partner_rate_limit_per_min: int = 60

    # LLM (этап 3): основной провайдер — GLM (Zhipu); OpenAI выпилен
    glm_api_key: str | None = None
    # Международный эндпоинт z.ai; для ключей open.bigmodel.cn задайте
    # GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
    glm_base_url: str = "https://api.z.ai/api/paas/v4"
    anthropic_api_key: str | None = None
    llm_default_model: str = "glm-4.6"
    llm_advanced_model: str = "glm-4.6"
    # glm | anthropic | ollama | mock ("mock" — для тестов/dev без LLM)
    llm_provider: str = "glm"
    llm_base_url: str | None = None  # переопределение (например, Ollama/прокси)
    llm_temperature: float = 0.2

    # AI-диалог (ТЗ п. 7.6, 7.7)
    dialog_ttl_sec: int = 60 * 60 * 24  # 24 часа
    dialog_history_len: int = 8  # последние N сообщений в контексте
    faq_cache_ttl_sec: int = 86400

    # RAG-сервис (план внедрения RAG, фазы 3-4; внешний rag_service)
    # Пустой URL = RAG выключен → answer_question отвечает через LLM (fallback).
    rag_service_url: str | None = None  # напр. http://rag.internal:8100
    rag_api_token: str | None = None  # Bearer-токен для RAG Service
    rag_enabled: bool = True  # feature flag (rollout 10% → 100%)
    rag_timeout_sec: int = 5
    rag_cache_ttl_sec: int = 3600  # кэш ответов в Redis, TTL 1 час
    rag_confidence_threshold: float = 0.6  # ниже — fallback на LLM
    rag_cb_fail_threshold: int = 5  # circuit breaker: N ошибок подряд
    rag_cb_cooldown_sec: int = 300  # пауза после открытия, 5 мин

    # Уведомления (этап 4, ТЗ п. 4.6, 8.6)
    notify_rate_limit_sec: int = 30  # не чаще 1 сообщения / 30 сек (п. 8.6)
    # Биллинг партнёров (P2-14): CPL-тарифы по планам, USD за лид.
    # Индивидуальный cpl_rate партнёра перекрывает план.
    partner_cpl_rates: dict[str, float] = Field(
        default_factory=lambda: {"starter": 5.0, "pro": 3.0, "salon": 2.0}
    )
    # Token Bucket Telegram (ТЗ п. 12.6): ~30 msg/s глобально, ~1 msg/s на чат.
    # 0 = лимитер выключен; Redis лежит → fail-open (429-обработка — страховка).
    telegram_rate_global_per_sec: float = 30.0
    telegram_rate_chat_per_sec: float = 1.0
    notify_scan_window_sec: int = 1800  # «свежие» объявления для сканирования фильтров
    notify_sent_ttl_sec: int = 60 * 60 * 24 * 7  # notified:{uid}:{lid}, 7 дней
    digest_max_cards: int = 5  # 3–5 карточек в дайджесте
    notify_quiet_hours_default: str | None = None  # глобальные тихие часы "23:00-08:00"

    # Отслеживание объявлений (миграция 0009, stale-cron фазы 3)
    stale_max_age_days: int = 3  # не виден N дней → is_active=false (CB-источники пропускаются)

    # Перевод тайтлов (PLAN.md, фаза 5; сервис services/translate).
    # Перевод некритичен: выключен флагом или пустым URL → показывается оригинал.
    translate_enabled: bool = False  # feature flag
    translate_service_url: str = ""  # напр. http://translate:8200
    translate_api_token: str | None = None  # Bearer; = API_TOKEN сервиса
    translate_timeout_sec: float = 30.0  # NLLB на CPU: батч может идти долго
    translate_batch_size: int = 32
    translate_cache_ttl_sec: int = 60 * 60 * 24 * 30  # tr:{tgt}:{src}:{sha1}, 30 дней
    translate_cb_fail_threshold: int = 3  # circuit breaker: N ошибок подряд
    translate_cb_cooldown_sec: int = 300
    translate_target_lang: str = "rus_Cyrl"  # NLLB-код целевого языка
    # NLLB-код исходного языка по источнику; "auto" — детект по письменности в сервисе
    translate_source_langs: dict[str, str] = {
        "encar": "kor_Hang",
        "kcar": "kor_Hang",
        "che168": "zho_Hans",
    }

    # S3/MinIO: raw_payload в холодное хранилище (PLAN.md, фаза 6).
    # aioboto3 (не блокирует event loop воркеров); MinIO — через endpoint_url.
    # Выключено (флаг/пустой endpoint) → raw не пишется никуда, объявления
    # сохраняются как обычно (fallback обязателен).
    raw_payload_enabled: bool = False  # feature flag
    s3_endpoint_url: str = ""  # напр. http://localhost:9000 (пусто = выключен)
    s3_bucket: str = "raw-payloads"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    raw_retention_days: int = 180  # retention-крон: удалять объекты старше N дней
    raw_presign_ttl_sec: int = 600  # presigned GET для админки, 10 мин

    # Прокси DataImpulse (опционально): шлюз для парсеров, если источники глушат.
    # Официальный формат: http://login__cr.cn;sessid.X:password@gw.dataimpulse.com:823
    # Задаёте user+pass → источники с capabilities.use_proxy=true ходят через шлюз.
    proxy_dataimpulse_user: str | None = None
    proxy_dataimpulse_pass: str | None = None
    proxy_dataimpulse_host: str = "gw.dataimpulse.com"
    proxy_dataimpulse_port: int = 823  # 823 = HTTPS, 824 = SOCKS5
    proxy_dataimpulse_geo: str | None = None  # cr.*: "cn" / "cn,de" (None = весь мир)
    # 0 = ротация IP на каждый запрос (default у провайдера); иначе sticky-окно
    proxy_dataimpulse_sticky_ttl_sec: int = 1800
    proxy_dataimpulse_fail_threshold: int = 5  # ConnectError подряд → пауза шлюза
    proxy_dataimpulse_fail_cooldown_sec: int = 300  # пауза: ходим напрямую

    # --- Микросервисы (профиль micro; gateway маршрутизирует /api/*) ---
    gateway_timeout_sec: float = 15.0
    svc_identity_url: str = "http://localhost:8101"
    svc_catalog_url: str = "http://localhost:8102"
    svc_billing_url: str = "http://localhost:8103"
    svc_partners_url: str = "http://localhost:8104"
    svc_dialog_url: str = "http://localhost:8105"
    svc_admin_url: str = "http://localhost:8106"

    # Alerting / мониторинг (этап 8, ТЗ п. 10)
    alerts_enabled: bool = True  # алерты в Telegram на ADMIN_CHAT_ID
    alert_dedup_ttl_sec: int = 1800  # базовое подавление повторов alert:{key}
    dlq_alert_threshold: int = 10  # глубина dead_letter для алерта
    health_check_interval_sec: int = 300  # период мониторинга DLQ в воркерах
    source_cb_error_streak: int = 5  # ошибок подряд до открытия CB источника


    # Monitoring
    sentry_dsn: str | None = None
    log_level: str = "INFO"

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
