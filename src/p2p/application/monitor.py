"""Live monitor state shared by every driving adapter (terminal and web)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from p2p.application.ports import SpotPricePort
from p2p.application.use_cases import GetMarketSnapshot
from p2p.domain import MarketSnapshot, is_stablecoin


@dataclass(frozen=True, slots=True)
class Config:
    assets: tuple[str, ...]
    fiat: str
    rows: int
    interval: float
    pay_types: tuple[str, ...]
    once: bool


@dataclass
class MonitorState:
    """Latest good snapshot (and when it was fetched) and latest error per asset."""

    snapshots: dict[str, MarketSnapshot] = field(default_factory=dict)
    snapshot_times: dict[str, datetime] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    updated_at: datetime | None = None
    # USDT spot price per non-stablecoin asset (last good) and the latest error.
    spot_prices: dict[str, Decimal] = field(default_factory=dict)
    spot_error: str | None = None


async def refresh(
    use_case: GetMarketSnapshot,
    config: Config,
    state: MonitorState,
    *,
    spot: SpotPricePort | None = None,
) -> None:
    """Fetch every asset (and, with ``spot``, the USDT spot prices of the
    non-stablecoins) concurrently; failures are recorded, never raised."""

    async def one(asset: str) -> None:
        try:
            state.snapshots[asset] = await use_case.execute(
                asset=asset,
                fiat=config.fiat,
                rows=config.rows,
                pay_types=config.pay_types,
            )
            state.snapshot_times[asset] = datetime.now()
            state.errors.pop(asset, None)
        except Exception as exc:  # noqa: BLE001 - the monitor must survive any refresh error
            state.errors[asset] = f"{type(exc).__name__}: {exc}"

    async def spot_prices(port: SpotPricePort, assets: tuple[str, ...]) -> None:
        try:
            state.spot_prices.update(await port.fetch_usdt_prices(assets))
            state.spot_error = None
        except Exception as exc:  # noqa: BLE001 - spot prices are a nice-to-have
            state.spot_error = f"{type(exc).__name__}: {exc}"

    fetches = [one(asset) for asset in config.assets]
    spot_assets = tuple(asset for asset in config.assets if not is_stablecoin(asset))
    if spot is not None and spot_assets:
        fetches.append(spot_prices(spot, spot_assets))
    await asyncio.gather(*fetches)
    state.updated_at = datetime.now()
