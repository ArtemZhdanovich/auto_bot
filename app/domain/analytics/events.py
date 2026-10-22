"""Канонические события воронки (P1-7).

Имена — часть домена: по ним считаются конверсии, поэтому они не могут
жить в инфраструктурном сервисе или в роутере.
"""

from __future__ import annotations

EVENT_USER_REGISTERED = "user_registered"
EVENT_SEARCH_COMPLETED = "search_completed"
EVENT_DIALOG_MESSAGE = "dialog_message"
EVENT_AI_SEARCH = "ai_search"  # выдача результата AI-подбора (preview/finalize)
EVENT_LEAD_CREATED = "lead_created"  # создатель лидов появится позже — зарезервировано
EVENT_LEAD_SOLD = "partner_lead_claimed"  # P2-10: лид забран партнёром (CPL)
EVENT_LEAD_FEEDBACK = "partner_lead_feedback"  # P2-10: фидбек партнёра по качеству
EVENT_SUBSCRIPTION_ACTIVATED = "subscription_activated"
