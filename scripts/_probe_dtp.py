import asyncio

from sqlalchemy import select

from app.infrastructure.db.models import Listing
from app.infrastructure.db.session import session_factory
from app.infrastructure.sources.common.http import HttpClient
from app.infrastructure.sources.encar import EncarApiAdapter
from urllib.parse import quote

async def main():
    adapter = EncarApiAdapter(
        source_name="encar", http_client=HttpClient(timeout=15)
    )
    async with session_factory() as session:
        rows = (await session.execute(
            select(Listing.id, Listing.source_id).where(Listing.source == "encar")
        )).all()[:5]
    for lid, sid in rows:
        try:
            detail = await adapter.http.get_json(
                adapter._detail_api.format(source_id=sid)
            )
        except Exception as e:
            print(lid, sid, "DETAIL ERR", str(e)[:60])
            continue
        vno = str(detail.get("vehicleNo") or "").strip()
        print(lid, sid, "vno:", vno)
        if not vno:
            continue
        try:
            ro = await adapter.http.get_json(
                adapter._accident_open_api.format(source_id=sid)
                + f"?vehicleNo={quote(vno)}"
            )
        except Exception as e:
            print("   OPEN ERR", str(e)[:80])
            continue
        print("   open:", type(ro).__name__, str(ro)[:80] if isinstance(ro, dict) else ro)
    await adapter.close()

asyncio.run(main())
