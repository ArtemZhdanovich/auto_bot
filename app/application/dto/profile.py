"""DTO профиля: публичная проекция пользователя для `/api/me`.

Секреты (`password_hash`, `email`, токены) в DTO не попадают by design:
сценарий отдаёт ровно те поля, что перечислены в `UserProfile`, поэтому
утечка возможна только осознанной правкой этого файла.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.dto.users import UserProfile

__all__ = ["OnboardingResult", "UserProfile"]


@dataclass(frozen=True, slots=True)
class OnboardingResult:
    """Итог `POST /api/me/onboarded`.

    `first_time` — вызов действительно проставил `onboarded_at` (а не был
    повтором): по нему пишется лог, первое значение не перезаписывается.
    """

    profile: UserProfile
    first_time: bool
