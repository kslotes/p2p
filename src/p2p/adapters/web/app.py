"""FastAPI driving adapter: JSON snapshots, SSE live updates, round-trip fees
and the static browser page at ``/``.

Money is serialized as decimal strings, never floats.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from p2p.adapters.web.poller import SnapshotPoller
from p2p.application import GetMarketSnapshot
from p2p.application.monitor import Config
from p2p.domain import Ad, round_trip

KEEPALIVE_SECONDS = 15  # comment line so proxies and browsers keep the stream open
PERCENT_PLACES = Decimal("0.0001")
STATIC_DIR = Path(__file__).parent / "static"


def _dec(value: Decimal) -> str:
    return format(value, "f")  # plain notation: 1000, not 1E+3


def _time(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _ad(ad: Ad) -> dict[str, Any]:
    return {
        "price": _dec(ad.price),
        "available": _dec(ad.available),
        "min_amount": _dec(ad.min_amount),
        "max_amount": _dec(ad.max_amount),
        "payment_methods": list(ad.payment_methods),
        "advertiser": {
            "nick": ad.advertiser.nick,
            "month_orders": ad.advertiser.month_orders,
            "finish_rate": _dec(ad.advertiser.finish_rate),
        },
    }


def snapshots_payload(poller: SnapshotPoller) -> dict[str, Any]:
    """Latest cached view of every configured asset, in configured order."""
    config, state = poller.config, poller.state
    assets = []
    for asset in config.assets:
        snapshot = state.snapshots.get(asset)
        ask = snapshot.best_ask if snapshot else None
        bid = snapshot.best_bid if snapshot else None
        spread = snapshot.spread if snapshot else None
        assets.append(
            {
                "asset": asset,
                "fiat": config.fiat,
                "best_buy": _dec(ask.price) if ask else None,
                "best_sell": _dec(bid.price) if bid else None,
                "spread": {
                    "absolute": _dec(spread.absolute),
                    "percentage": _dec(spread.percentage.quantize(PERCENT_PLACES)),
                }
                if spread
                else None,
                "asks": [_ad(ad) for ad in snapshot.asks] if snapshot else [],
                "bids": [_ad(ad) for ad in snapshot.bids] if snapshot else [],
                "updated_at": _time(state.snapshot_times.get(asset)),
                "error": state.errors.get(asset),
            }
        )
    return {
        "fiat": config.fiat,
        "interval": config.interval,
        "pay_types": list(config.pay_types),
        "updated_at": _time(state.updated_at),
        "assets": assets,
    }


def _event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def sse_stream(poller: SnapshotPoller) -> AsyncIterator[str]:
    """Current payload first, then one event per refresh until the poller stops."""
    changed = poller.changed
    yield _event(snapshots_payload(poller))
    while poller.running:
        try:
            async with asyncio.timeout(KEEPALIVE_SECONDS):
                await changed.wait()
        except TimeoutError:
            yield ": keepalive\n\n"
            continue
        if not poller.running:
            return
        changed = poller.changed
        yield _event(snapshots_payload(poller))


def create_app(
    use_case: GetMarketSnapshot,
    config: Config,
    *,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    poller = SnapshotPoller(use_case, config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await poller.start()
        try:
            yield
        finally:
            await poller.stop()
            if on_shutdown is not None:
                await on_shutdown()

    app = FastAPI(title="Binance P2P monitor", lifespan=lifespan)
    app.state.poller = poller

    @app.get("/api/snapshots")
    async def snapshots() -> dict[str, Any]:
        return snapshots_payload(poller)

    @app.get("/api/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(
            sse_stream(poller),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/round-trip")
    async def round_trip_endpoint(
        sell: Annotated[Decimal, Query(gt=0, description="Sell price (fiat).")],
        rebuy: Annotated[Decimal, Query(gt=0, description="Rebuy price (fiat).")],
        fee_pct: Annotated[
            Decimal, Query(ge=0, lt=100, description="Maker fee percent, 0.2 = 0.20%.")
        ],
    ) -> dict[str, str]:
        try:
            result = round_trip(sell=sell, rebuy=rebuy, fee_rate=fee_pct / 100)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "sell": _dec(sell),
            "rebuy": _dec(rebuy),
            "fee_pct": _dec(fee_pct),
            "real_sell": _dec(result.real_sell),
            "real_rebuy": _dec(result.real_rebuy),
            "difference": _dec(result.difference),
            "difference_pct": _dec(result.difference_pct),
            "outcome": result.outcome.value,
        }

    # Mounted last so the /api routes above take precedence.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
