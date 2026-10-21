import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from p2p.application.order_sync import (
    BACKFILL,
    OVERLAP,
    WINDOW,
    SyncOrderHistory,
    split_windows,
)
from p2p.application.ports import OrderHistorySource, OrderStore
from p2p.domain.orders import Order, OrderRole, OrderSide, OrderStatus

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def make_order(
    number: str,
    created_at: datetime,
    *,
    side: OrderSide = OrderSide.SELL,
    status: OrderStatus = OrderStatus.COMPLETED,
) -> Order:
    return Order(
        order_number=number,
        adv_no="adv",
        side=side,
        asset="USDT",
        fiat="ARS",
        fiat_symbol="$",
        amount=Decimal(10),
        total_price=Decimal(15000),
        unit_price=Decimal(1500),
        status=status,
        created_at=created_at,
        role=OrderRole.MAKER,
        commission=Decimal("0.02"),
        taker_commission=None,
        counterparty="x",
        pay_method="Bank",
    )


class FakeSource:
    def __init__(self, orders: Iterable[Order] = ()) -> None:
        self.orders = list(orders)
        self.calls: list[tuple[OrderSide, datetime, datetime]] = []

    async def fetch(
        self, side: OrderSide, start: datetime, end: datetime
    ) -> list[Order]:
        self.calls.append((side, start, end))
        return [
            order
            for order in self.orders
            if order.side is side and start <= order.created_at <= end
        ]


class FakeStore:
    def __init__(self, orders: Iterable[Order] = ()) -> None:
        self.rows = {order.order_number: order for order in orders}

    def upsert(self, orders: Iterable[Order]) -> int:
        count = 0
        for order in orders:
            self.rows[order.order_number] = order
            count += 1
        return count

    def query(
        self,
        *,
        asset: str | None = None,
        side: OrderSide | None = None,
        status: OrderStatus | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Order]:
        return sorted(self.rows.values(), key=lambda o: o.created_at, reverse=True)

    def latest_created_at(self) -> datetime | None:
        return max((o.created_at for o in self.rows.values()), default=None)

    def oldest_open_created_at(self) -> datetime | None:
        return min(
            (o.created_at for o in self.rows.values() if not o.is_final), default=None
        )


def run_sync(source: FakeSource, store: FakeStore):
    return asyncio.run(SyncOrderHistory(source, store, now=lambda: NOW).execute())


def test_fakes_satisfy_the_ports():
    source: OrderHistorySource = FakeSource()
    store: OrderStore = FakeStore()
    assert source is not None and store is not None


def test_split_windows_covers_the_range_with_windows_of_at_most_30_days():
    start = NOW - timedelta(days=65)

    windows = split_windows(start, NOW)

    assert windows == [
        (start, start + WINDOW),
        (start + WINDOW, start + 2 * WINDOW),
        (start + 2 * WINDOW, NOW),
    ]
    assert all(end - begin <= timedelta(days=30) for begin, end in windows)


def test_split_windows_of_an_empty_range_is_empty():
    assert split_windows(NOW, NOW) == []
    assert split_windows(NOW, NOW - timedelta(days=1)) == []


def test_empty_store_backfills_180_days_both_sides_per_window():
    orders = [
        make_order("old", NOW - timedelta(days=170)),
        make_order("mid", NOW - timedelta(days=95), side=OrderSide.BUY),
        make_order("new", NOW - timedelta(hours=1)),
        make_order("too-old", NOW - timedelta(days=200)),
    ]
    source, store = FakeSource(orders), FakeStore()

    result = run_sync(source, store)

    assert BACKFILL == timedelta(days=180)
    assert result.start == NOW - BACKFILL and result.end == NOW
    assert result.windows == 6
    assert len(source.calls) == 12
    assert {side for side, _, _ in source.calls} == {OrderSide.BUY, OrderSide.SELL}
    assert source.calls[0][1] == NOW - BACKFILL
    assert source.calls[-1][2] == NOW
    assert set(store.rows) == {"old", "mid", "new"}
    assert (result.fetched, result.upserted) == (3, 3)


def test_incremental_sync_starts_one_day_before_the_latest_order():
    latest = NOW - timedelta(days=10)
    store = FakeStore([make_order("a", latest)])
    source = FakeSource([make_order("b", NOW - timedelta(days=2))])

    result = run_sync(source, store)

    assert OVERLAP == timedelta(days=1)
    assert result.start == latest - OVERLAP
    assert result.windows == 1
    assert set(store.rows) == {"a", "b"}


def test_incremental_sync_rechecks_the_oldest_non_final_order():
    open_at = NOW - timedelta(days=50)
    store = FakeStore(
        [
            make_order("pending", open_at, status=OrderStatus.TRADING),
            make_order("done", NOW - timedelta(days=3)),
        ]
    )
    source = FakeSource([make_order("pending", open_at)])  # now COMPLETED

    result = run_sync(source, store)

    assert result.start == open_at - OVERLAP
    assert result.windows == 2
    assert store.rows["pending"].status is OrderStatus.COMPLETED


def test_incremental_sync_never_starts_before_the_backfill_horizon():
    store = FakeStore(
        [make_order("ancient", NOW - timedelta(days=400), status=OrderStatus.PENDING)]
    )

    result = run_sync(FakeSource(), store)

    assert result.start == NOW - BACKFILL


def test_orders_seen_in_two_windows_are_upserted_once():
    boundary = NOW - timedelta(days=30)
    store = FakeStore([make_order("seed", NOW - timedelta(days=40))])

    class EdgeSource(FakeSource):
        """Returns the boundary order in every SELL window, as Binance may."""

        async def fetch(self, side, start, end):
            self.calls.append((side, start, end))
            return list(self.orders) if side is OrderSide.SELL else []

    result = run_sync(EdgeSource([make_order("edge", boundary)]), store)

    assert result.windows == 2
    assert result.fetched == 2
    assert result.upserted == 1


def test_failure_in_a_later_window_keeps_earlier_windows_stored():
    store = FakeStore()

    class FailingSource(FakeSource):
        async def fetch(self, side, start, end):
            self.calls.append((side, start, end))
            if len(self.calls) > 2:
                raise RuntimeError("boom")
            return [make_order(f"{side.value}-1", start, side=side)]

    with pytest.raises(RuntimeError):
        run_sync(FailingSource(), store)

    assert set(store.rows) == {"BUY-1", "SELL-1"}
