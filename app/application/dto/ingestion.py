"""DTO ингеста для прикладного слоя.

`IngestStats` — результат сценария `IngestListings`. Тип объявлен в
`domain/listings/ingestion.py`, потому что его поля вычисляет доменное
правило `summarize`; здесь — точка импорта для прикладного и
инфраструктурного слоёв (транспорт не должен ходить в домен напрямую).
"""

from __future__ import annotations

from app.domain.listings.ingestion import IngestStats, PendingRawUpload

__all__ = ["IngestStats", "PendingRawUpload"]
