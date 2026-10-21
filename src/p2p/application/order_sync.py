"""Keep the local order store in step with the Binance order history."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from p2p.application.ports import OrderHistorySource, OrderStore
from p2p.domain.orders import OrderSide

# Binance serves only the last 6 months; stay a little inside that horizon.
BACKFILL = timedelta(days=180)
# The history endpoint accepts at most 30 days between start and end.
WINDOW = timedelta(days=30)
# Re-read a day before the cursor so late or clock-skewed orders are not missed.
OVERLAP = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class SyncResult:
    start: datetime
    end: datetime
    windows: int
    fetched: int  # rows returned by the source, duplicates included
    upserted: int  # distinct orders written to the store


def split_windows(
    start: datetime, end: datetime, size: timedelta = WINDOW
) -> list[tuple[datetime, datetime]]:
    """Consecutive ``(start, end)`` windows of at most ``size`` covering the range."""
    windows = []
    cursor = start
    while cursor < end:
        upper = min(cursor + size, end)
        windows.append((cursor, upper))
        cursor = upper
    return windows


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SyncOrderHistory:
    """Backfill 180 days on an empty store, then sync incrementally.

    The incremental start is the older of the newest stored order and the
    oldest non-final one (so status changes land), minus ``OVERLAP`` and never
    before the backfill horizon. Windows are fetched oldest first and stored
    one by one, so a failure keeps the progress made so far.
    """

    def __init__(
        self,
        source: OrderHistorySource,
        store: OrderStore,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._source = source
        self._store = store
        self._now = now

    def _start(self, now: datetime) -> datetime:
        horizon = now - BACKFILL
        cursors = [
            moment
            for moment in (
                self._store.latest_created_at(),
                self._store.oldest_open_created_at(),
            )
            if moment is not None
        ]
        if not cursors:
            return horizon
        return max(min(cursors) - OVERLAP, horizon)

    async def execute(self) -> SyncResult:
        now = self._now()
        start = self._start(now)
        windows = split_windows(start, now)
        fetched = upserted = 0
        seen: set[str] = set()
        for window_start, window_end in windows:
            batch = []
            for side in (OrderSide.BUY, OrderSide.SELL):
                orders = await self._source.fetch(side, window_start, window_end)
                fetched += len(orders)
                for order in orders:
                    if order.order_number not in seen:
                        seen.add(order.order_number)
                        batch.append(order)
            if batch:
                upserted += self._store.upsert(batch)
        return SyncResult(
            start=start,
            end=now,
            windows=len(windows),
            fetched=fetched,
            upserted=upserted,
        )
