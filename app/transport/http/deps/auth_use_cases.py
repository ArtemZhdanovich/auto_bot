"""Dishka-алиасы auth-сценариев для роутеров (сборка — в контейнере).

Раньше здесь был composition root на `Depends`; теперь сценарии собирает
Dishka-контейнер (`app/di/providers/use_cases.py` + `security.py`):
сценарии инжектят `UserRepository`/`LoginCodeStore`/`TokenIssuer`,
контейнер привязывает SQLAlchemy/Redis/JWT-адаптеры. Роутер получает
готовый сценарий через `FromDishka[...]` (роутер — с `DishkaRoute`).
"""

from __future__ import annotations

from dishka.integrations.fastapi import FromDishka

from app.application.auth.use_cases import (
    AuthenticateTelegram,
    CancelTelegramLogin,
    ConfirmTelegramLogin,
    CreateGuestSession,
    CreateTelegramLoginLink,
    GetLoginContext,
    LoginWebUser,
    PollTelegramLogin,
    RefreshSession,
    RegisterWebUser,
)

AuthenticateTelegramDep = FromDishka[AuthenticateTelegram]
CreateTelegramLoginLinkDep = FromDishka[CreateTelegramLoginLink]
PollTelegramLoginDep = FromDishka[PollTelegramLogin]
GetLoginContextDep = FromDishka[GetLoginContext]
CancelTelegramLoginDep = FromDishka[CancelTelegramLogin]
ConfirmTelegramLoginDep = FromDishka[ConfirmTelegramLogin]
RefreshSessionDep = FromDishka[RefreshSession]
RegisterWebUserDep = FromDishka[RegisterWebUser]
CreateGuestSessionDep = FromDishka[CreateGuestSession]
LoginWebUserDep = FromDishka[LoginWebUser]

