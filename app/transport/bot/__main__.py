"""Запуск бота: python -m app.transport.bot (ТЗ п. 4.1 — FSM в Redis)."""

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.transport.bot.button_flow import router as button_flow_router
from app.transport.bot.client import BackendClient
from app.transport.bot.handlers import router
from app.config import get_settings

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set — заполните .env")

    # FSM хранится в Redis: fsm:{user_id} (ТЗ п. 4.1)
    storage = RedisStorage.from_url(settings.redis_url)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)
    dp["backend"] = BackendClient()  # доступ из button_flow через message.bot
    dp.include_router(router)
    dp.include_router(button_flow_router)

    await bot.delete_webhook(drop_pending_updates=True)

    # Антифлуд: сообщения и колбэки — скользящее окно на пользователя.
    from app.transport.bot.throttle import FloodMiddleware

    flood = FloodMiddleware()
    dp.message.outer_middleware(flood)
    dp.callback_query.outer_middleware(flood)

    @dp.errors()
    async def on_error(event, exc: Exception) -> bool:
        """Глобальный обработчик: лог + вежливое сообщение вместо тишины."""
        logger.exception("handler_error: %s", exc)
        message = getattr(event, "message", None)
        cb = getattr(event, "callback_query", None)
        try:
            if message is not None:
                await message.answer(
                    "😔 Что-то пошло не так — я уже разбираюсь. Попробуйте ещё раз."
                )
            elif cb is not None:
                await cb.answer(
                    "Что-то сломалось, попробуйте ещё раз", show_alert=True
                )
        except Exception:  # уведомление не должно маскировать исходную ошибку
            logger.debug("error_notify_failed", exc_info=True)
        return True  # ошибку обработали — не роняем polling

    # TECHDEBT 7.4: Telegram API мог быть недоступен при старте — setup
    # команд/меню не должен ронять процесс бота.
    from aiogram.types import (
        BotCommand,
        BotCommandScopeAllPrivateChats,
        MenuButtonWebApp,
        WebAppInfo,
    )

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Запуск / главное меню"),
                BotCommand(command="calc", description="Калькулятор: цена $ объём год"),
                BotCommand(command="menu", description="Меню"),
                BotCommand(command="reset", description="Сбросить диалог подбора"),
                BotCommand(command="help", description="Помощь"),
                BotCommand(command="stop", description="Отписаться от уведомлений"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
        )
    except Exception:  # noqa: BLE001 — декоративный setup, не критичен
        logger.warning("set_my_commands_failed", exc_info=True)
    if settings.miniapp_url:
        if settings.miniapp_url.startswith("https://"):
            try:
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="🚗 Поиск авто",
                        web_app=WebAppInfo(url=settings.miniapp_url),
                    )
                )
            except Exception:  # noqa: BLE001
                logger.warning("set_chat_menu_button_failed", exc_info=True)
        else:
            # Telegram принимает только HTTPS для Web App: локальная демка
            # (http://localhost:…) — работаем без кнопки-меню, не падаем.
            logger.warning(
                "miniapp_url не HTTPS (%s) — кнопка-меню Mini App не установлена",
                settings.miniapp_url,
            )

    # Heartbeat для healthcheck (polling без HTTP-порта)
    from app.infrastructure.runtime.heartbeat import start_heartbeat

    heartbeat = await start_heartbeat("bot")
    try:
        await dp.start_polling(bot)
    finally:
        # 7.3: cancel() без await оставляет «висящую» задачу — AwaitWarning
        # и мусор в логах при shutdown; дожидаемся фактической остановки.
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        # 7.3: сессия FSM-хранилища (Redis) должна быть закрыта явно.
        storage = getattr(dp, "storage", None)
        close = getattr(storage, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()


if __name__ == "__main__":
    asyncio.run(main())
