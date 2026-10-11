"""Конфигурация приложения (ТЗ п. 11.6 — переменные окружения)."""

from functools import lru_cache

from pydantic import field_validator
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

    # LLM (этап 3): OpenAI-совместимые провайдеры; основной — GLM (Zhipu)
    glm_api_key: str | None = None
    # Международный эндпоинт z.ai; для ключей open.bigmodel.cn задайте
    # GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
    glm_base_url: str = "https://api.z.ai/api/paas/v4"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    llm_default_model: str = "glm-4.6"
    llm_advanced_model: str = "glm-4.6"
    # glm | openai | anthropic | ollama | mock ("mock" — для тестов/dev без LLM)
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
    notify_scan_window_sec: int = 1800  # «свежие» объявления для сканирования фильтров
    notify_sent_ttl_sec: int = 60 * 60 * 24 * 7  # notified:{uid}:{lid}, 7 дней
    digest_max_cards: int = 5  # 3–5 карточек в дайджесте
    notify_quiet_hours_default: str | None = None  # глобальные тихие часы "23:00-08:00"

    # Alerting / мониторинг (этап 8, ТЗ п. 10)
    alerts_enabled: bool = True  # алерты в Telegram на ADMIN_CHAT_ID
    alert_dedup_ttl_sec: int = 1800  # подавление повторов alert:{key}, 30 мин
    dlq_alert_threshold: int = 10  # глубина dead_letter для алерта
    health_check_interval_sec: int = 300  # период мониторинга DLQ в воркерах


    # Monitoring
    sentry_dsn: str | None = None
    log_level: str = "INFO"

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
