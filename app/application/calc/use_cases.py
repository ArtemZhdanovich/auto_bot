"""Тонкая прикладная обёртка над доменным калькулятором доставки."""

from __future__ import annotations

from app.domain.calc.delivery import DeliveryBreakdown, calc_delivery

__all__ = ["DeliveryBreakdown", "calc_delivery"]
