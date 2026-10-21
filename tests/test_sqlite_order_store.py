import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from p2p.adapters.sqlite_order_store import SCHEMA_VERSION, SqliteOrderStore
from p2p.application.ports import OrderStore
from p2p.domain.orders import Order, OrderRole, OrderSide, OrderStatus

BASE = datetime(2026, 9, 1, 12, 0, 0, 123000, tzinfo=UTC)


def make_order(
    number: str,
    *,
    days: int = 0,
    side: OrderSide = OrderSide.SELL,
    status: OrderStatus = OrderStatus.COMPLETED,
    asset: str = "USDT",
    commission: str | None = "0.07",
) -> Order:
    return Order(
        order_number=number,
        adv_no="adv-1",
        side=side,
        asset=asset,
        fiat="ARS",
        fiat_symbol="$",
        amount=Decimal("166.11"),
        total_price=Decimal("260998.5000"),
        unit_price=Decimal("1571.25"),
        status=status,
        created_at=BASE + timedelta(days=days),
        role=OrderRole.MAKER,
        commission=None if commission is None else Decimal(commission),
        taker_commission=None,
        counterparty="Buyer-Alpha",
        pay_method="BANK",
    )


@pytest.fixture
def store():
    with SqliteOrderStore(":memory:") as store:
        yield store


def test_store_satisfies_the_port(store):
    port: OrderStore = store
    assert port is not None


def test_round_trip_keeps_every_field_and_decimal_exactness(store):
    order = make_order("1")
    taker = replace(
        make_order("2", commission=None),
        role=OrderRole.TAKER,
        taker_commission=Decimal("0.0700"),
        side=OrderSide.BUY,
    )

    store.upsert([order, taker])
    loaded = {o.order_number: o for o in store.query()}

    assert loaded["1"] == order
    assert loaded["2"] == taker
    assert str(loaded["1"].unit_price) == "1571.25"
    assert str(loaded["1"].commission) == "0.07"
    assert str(loaded["1"].total_price) == "260998.5000"
    assert str(loaded["2"].taker_commission) == "0.0700"
    assert loaded["2"].commission is None
    assert loaded["1"].created_at == BASE and loaded["1"].created_at.tzinfo is UTC


def test_double_upsert_is_idempotent_and_status_changes_overwrite(store):
    order = make_order("1", status=OrderStatus.TRADING)

    assert store.upsert([order]) == 1
    assert store.upsert([order]) == 1
    store.upsert([replace(order, status=OrderStatus.COMPLETED)])

    (loaded,) = store.query()
    assert loaded.status is OrderStatus.COMPLETED


def test_upsert_of_nothing_writes_nothing(store):
    assert store.upsert([]) == 0
    assert store.query() == []


def test_query_is_newest_first_and_filters_combine(store):
    store.upsert(
        [
            make_order("a", days=0),
            make_order("b", days=2, side=OrderSide.BUY),
            make_order("c", days=4, status=OrderStatus.CANCELLED),
            make_order("d", days=6, asset="BTC"),
        ]
    )

    def numbers(**filters):
        return [o.order_number for o in store.query(**filters)]

    assert numbers() == ["d", "c", "b", "a"]
    assert numbers(asset="USDT") == ["c", "b", "a"]
    assert numbers(side=OrderSide.BUY) == ["b"]
    assert numbers(status=OrderStatus.COMPLETED) == ["d", "b", "a"]
    assert numbers(start=BASE + timedelta(days=2)) == ["d", "c", "b"]
    assert numbers(end=BASE + timedelta(days=4)) == ["b", "a"]  # end is exclusive
    assert numbers(asset="USDT", status=OrderStatus.COMPLETED, side=OrderSide.SELL) == [
        "a"
    ]


def test_cursors_track_newest_and_oldest_open_orders(store):
    assert store.latest_created_at() is None
    assert store.oldest_open_created_at() is None

    store.upsert(
        [
            make_order("a", days=0),
            make_order("b", days=1, status=OrderStatus.IN_APPEAL),
            make_order("c", days=3, status=OrderStatus.UNKNOWN),
            make_order("d", days=5, status=OrderStatus.CANCELLED_BY_SYSTEM),
        ]
    )

    assert store.latest_created_at() == BASE + timedelta(days=5)
    assert store.oldest_open_created_at() == BASE + timedelta(days=1)


def test_file_store_creates_parent_dirs_sets_schema_version_and_persists(tmp_path):
    path = tmp_path / "nested" / "dir" / "p2p.sqlite"

    with SqliteOrderStore(path) as store:
        store.upsert([make_order("1")])
    with SqliteOrderStore(path) as reopened:
        assert [o.order_number for o in reopened.query()] == ["1"]

    connection = sqlite3.connect(path)
    try:
        (version,) = connection.execute("PRAGMA user_version").fetchone()
    finally:
        connection.close()
    assert version == SCHEMA_VERSION >= 1


def test_amounts_are_stored_as_text(tmp_path):
    path = tmp_path / "p2p.sqlite"
    with SqliteOrderStore(path) as store:
        store.upsert([make_order("1")])

    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT typeof(amount), unit_price, typeof(created_at) FROM orders"
        ).fetchone()
    finally:
        connection.close()
    assert row == ("text", "1571.25", "integer")
