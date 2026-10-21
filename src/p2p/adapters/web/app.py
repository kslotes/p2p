"""FastAPI driving adapter: JSON snapshots, SSE live updates, round-trip fees,
the local order history and the static browser page at ``/``.

Money is serialized as decimal strings, never floats.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from p2p.adapters.web.order_sync_runner import OrderSyncRunner
from p2p.adapters.web.poller import SnapshotPoller
from p2p.application import GetMarketSnapshot, OrderStore
from p2p.application.monitor import Config
from p2p.domain import Ad, estimate, round_trip
from p2p.domain.fees import DEFAULT_NOTIONAL, MAX_NOTIONAL
from p2p.domain.orders import Order, OrderSide, OrderStatus, OrderSummary, summarize

KEEPALIVE_SECONDS = 15  # comment line so proxies and browsers keep the stream open
PERCENT_PLACES = Decimal("0.0001")
STATIC_DIR = Path(__file__).parent / "static"
ASSET_PATTERN = r"^[A-Za-z0-9]{1,20}$"
# Binance reports a commission on cancelled orders that was never charged.
UNCHARGED_STATUSES = frozenset({OrderStatus.CANCELLED, OrderStatus.CANCELLED_BY_SYSTEM})


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
            "verified": ad.advertiser.verified,
            "merchant_identity": ad.advertiser.merchant_identity,
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


def _order(order: Order) -> dict[str, Any]:
    return {
        "order_number": order.order_number,
        "created_at": _time(order.created_at),
        "side": order.side.value,
        "asset": order.asset,
        "fiat": order.fiat,
        "amount": _dec(order.amount),
        "unit_price": _dec(order.unit_price),
        "total_price": _dec(order.total_price),
        "fee": None if order.status in UNCHARGED_STATUSES else _dec(order.fee),
        "role": order.role.value,
        "status": order.status.value,
        "counterparty": order.counterparty,
        "pay_method": order.pay_method,
    }


def _totals(summary: OrderSummary) -> dict[str, Any]:
    # No grand total of amounts or fees: it would mix assets and fiats.
    return {
        "count": summary.count,
        "groups": [
            {
                "side": group.side.value,
                "asset": group.asset,
                "fiat": group.fiat,
                "count": group.count,
                "amount": _dec(group.amount),
                "total_price": _dec(group.total_price),
                "fees": _dec(group.fees),
            }
            for group in summary.groups
        ],
    }


def sync_status(runner: OrderSyncRunner) -> dict[str, Any]:
    return {
        "configured": runner.configured,
        "running": runner.running,
        "interval_seconds": runner.interval if runner.configured else None,
        "last_attempt": _time(runner.last_attempt),
        "last_success": _time(runner.last_success),
        "last_error": runner.last_error,
    }


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)


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
    orders: OrderStore | None = None,
    order_sync: OrderSyncRunner | None = None,
) -> FastAPI:
    """``orders`` is the local order database (None when there is none yet);
    ``order_sync`` keeps it in step with Binance (not configured by default)."""
    poller = SnapshotPoller(use_case, config)
    runner = order_sync if order_sync is not None else OrderSyncRunner(None)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await poller.start()
        await runner.start()
        try:
            yield
        finally:
            await runner.stop()
            await poller.stop()
            if on_shutdown is not None:
                await on_shutdown()

    app = FastAPI(title="Binance P2P monitor", lifespan=lifespan)
    app.state.poller = poller
    app.state.order_sync = runner

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
        amount: Annotated[
            Decimal,
            Query(
                gt=0,
                le=MAX_NOTIONAL,
                description="Asset amount for the estimate (1000 USDT by default).",
            ),
        ] = DEFAULT_NOTIONAL,
    ) -> dict[str, str]:
        try:
            result = round_trip(sell=sell, rebuy=rebuy, fee_rate=fee_pct / 100)
            scaled = estimate(result, notional=amount)
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
            "amount": _dec(scaled.notional),
            "fiat_result": _dec(scaled.fiat_result),
            "asset_result": _dec(scaled.asset_result),
        }

    # Plain `def`: SQLite reads block, so FastAPI runs this in its threadpool.
    @app.get("/api/orders")
    def orders_endpoint(
        asset: Annotated[
            str | None, Query(pattern=ASSET_PATTERN, description="e.g. USDT.")
        ] = None,
        side: Annotated[
            OrderSide | None, Query(description="Your side of the order.")
        ] = None,
        status: OrderStatus | None = None,
        start: Annotated[
            date | None,
            Query(alias="from", description="First UTC day included (YYYY-MM-DD)."),
        ] = None,
        end: Annotated[
            date | None,
            Query(alias="to", description="Last UTC day included (YYYY-MM-DD)."),
        ] = None,
    ) -> dict[str, Any]:
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=422, detail="from must not be after to")
        found: list[Order] = []
        assets: list[str] = []
        if orders is not None:
            found = orders.query(
                asset=asset.upper() if asset else None,
                side=side,
                status=status,
                start=_day_start(start) if start else None,
                end=_day_start(end + timedelta(days=1)) if end else None,
            )
            assets = sorted({order.asset for order in orders.query()})
        return {
            "orders": [_order(order) for order in found],
            "totals": _totals(summarize(found)),
            "assets": assets,
            "sync": sync_status(runner),
        }

    @app.get("/api/orders/sync")
    async def order_sync_status() -> dict[str, Any]:
        return sync_status(runner)

    @app.post("/api/orders/sync")
    async def order_sync_now() -> dict[str, Any]:
        if not runner.configured:
            raise HTTPException(
                status_code=409, detail="order history sync is not configured"
            )
        runner.trigger()  # no-op while a sync is already running
        return sync_status(runner)

    # Mounted last so the /api routes above take precedence.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
