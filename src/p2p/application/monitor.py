"""Live monitor state shared by every driving adapter (terminal and web)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from p2p.application.use_cases import GetMarketSnapshot
from p2p.domain import MarketSnapshot


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


async def refresh(
    use_case: GetMarketSnapshot, config: Config, state: MonitorState
) -> None:
    """Fetch every asset concurrently; failures are recorded, never raised."""

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

    await asyncio.gather(*(one(asset) for asset in config.assets))
    state.updated_at = datetime.now()
