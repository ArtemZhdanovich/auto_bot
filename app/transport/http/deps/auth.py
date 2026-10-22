"""Аутентификация Mini App (Bearer JWT): только алиас для роутеров.

Вся сборка — в Dishka-контейнере (`deps/providers.py::HttpDepsProvider`):
`Request` приходит из dishka-контекста (`from_context`), токен проверяет
порт `AccessTokenVerifier` (секрет — из `Settings` в контейнере, транспорт
его не читает), профиль и проверку активности делает сценарий
`GetCurrentUser`. FastAPI `Depends` не используется: эндпоинты объявляют
`user: CurrentUserDep` при `route_class=DishkaRoute`.
"""

from __future__ import annotations

from dishka.integrations.fastapi import FromDishka

from app.application.identity.dto import CurrentUser

CurrentUserDep = FromDishka[CurrentUser]
