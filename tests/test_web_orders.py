import asyncio
import logging
import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from p2p.adapters.sqlite_order_store import SqliteOrderStore
from p2p.adapters.web import create_app
from p2p.adapters.web.main import build_order_history, parse_args
from p2p.adapters.web.order_sync_runner import OrderSyncRunner, safe_error
from p2p.application import GetMarketSnapshot
from p2p.application.monitor import Config
from p2p.domain.orders import (
    Order,
    OrderHistoryError,
    OrderRole,
    OrderSide,
    OrderStatus,
)

CREDENTIALS_ENV = {"BINANCE_API_KEY": "test-key", "BINANCE_API_SECRET": "test-secret"}


class FakeMarketData:
    async def fetch_ads(self, asset, fiat, side, rows, pay_types=()):
        return []


def make_config() -> Config:
    return Config(
        assets=("USDT",), fiat="ARS", rows=5, interval=3600, pay_types=(), once=False
    )


def make_order(
    number: str,
    *,
    created: datetime,
    side: OrderSide = OrderSide.SELL,
    status: OrderStatus = OrderStatus.COMPLETED,
    role: OrderRole = OrderRole.MAKER,
    asset: str = "USDT",
    amount: str = "100",
    total_price: str = "150000",
    commission: str | None = "0.2",
) -> Order:
    return Order(
        order_number=number,
        adv_no="adv-1",
        side=side,
        asset=asset,
        fiat="ARS",
        fiat_symbol="$",
        amount=Decimal(amount),
        total_price=Decimal(total_price),
        unit_price=Decimal(total_price) / Decimal(amount),
        status=status,
        created_at=created,
        role=role,
        commission=None if commission is None else Decimal(commission),
        taker_commission=None,
        counterparty="party-a",
        pay_method="Bank",
    )


def sample_store() -> SqliteOrderStore:
    store = SqliteOrderStore(":memory:")
    store.upsert(
        [
            make_order("1", created=datetime(2026, 9, 1, 12, tzinfo=UTC)),
            make_order(
                "2",
                created=datetime(2026, 9, 2, 23, 30, tzinfo=UTC),
                side=OrderSide.BUY,
                amount="50",
                total_price="76000",
            ),
            make_order(
                "3",
                created=datetime(2026, 9, 3, 8, tzinfo=UTC),
                status=OrderStatus.CANCELLED,
            ),
            make_order(
                "4",
                created=datetime(2026, 9, 4, 8, tzinfo=UTC),
                asset="BTC",
                amount="0.001",
                total_price="120000",
                commission="0.000002",
            ),
        ]
    )
    return store


def make_app(store=None, runner=None):
    return create_app(
        GetMarketSnapshot(FakeMarketData()),
        make_config(),
        orders=store,
        order_sync=runner,
    )


# ---------- /api/orders


def test_orders_are_listed_newest_first_with_totals_over_completed_only():
    client = TestClient(make_app(sample_store()))

    response = client.get("/api/orders")

    assert response.status_code == 200
    body = response.json()
    assert [order["order_number"] for order in body["orders"]] == ["4", "3", "2", "1"]
    assert body["orders"][3] == {
        "order_number": "1",
        "created_at": "2026-09-01T12:00:00+00:00",
        "side": "SELL",
        "asset": "USDT",
        "fiat": "ARS",
        "amount": "100",
        "unit_price": "1500",
        "total_price": "150000",
        "fee": "0.2",
        "role": "MAKER",
        "status": "COMPLETED",
        "counterparty": "party-a",
        "pay_method": "Bank",
    }
    # Binance reports a commission on cancelled orders that was never charged.
    assert body["orders"][1]["fee"] is None
    totals = body["totals"]
    assert totals["count"] == 3  # the cancelled order is ignored
    assert totals["groups"] == [
        {
            "side": "BUY",
            "asset": "USDT",
            "fiat": "ARS",
            "count": 1,
            "amount": "50",
            "total_price": "76000",
            "fees": "0.2",
        },
        {
            "side": "SELL",
            "asset": "BTC",
            "fiat": "ARS",
            "count": 1,
            "amount": "0.001",
            "total_price": "120000",
            "fees": "0.000002",
        },
        {
            "side": "SELL",
            "asset": "USDT",
            "fiat": "ARS",
            "count": 1,
            "amount": "100",
            "total_price": "150000",
            "fees": "0.2",
        },
    ]
    assert body["assets"] == ["BTC", "USDT"]
    assert body["sync"]["configured"] is False


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"asset": "usdt"}, ["3", "2", "1"]),
        ({"side": "BUY"}, ["2"]),
        ({"status": "CANCELLED"}, ["3"]),
        ({"from": "2026-09-02"}, ["4", "3", "2"]),
        # `to` is inclusive: the whole UTC day counts
        ({"to": "2026-09-02"}, ["2", "1"]),
        ({"from": "2026-09-02", "to": "2026-09-03", "side": "SELL"}, ["3"]),
    ],
)
def test_orders_can_be_filtered(params, expected):
    client = TestClient(make_app(sample_store()))

    body = client.get("/api/orders", params=params).json()

    assert [order["order_number"] for order in body["orders"]] == expected
    assert body["assets"] == ["BTC", "USDT"]  # options do not shrink with filters


def test_filtered_totals_only_cover_the_matching_orders():
    client = TestClient(make_app(sample_store()))

    totals = client.get("/api/orders", params={"status": "CANCELLED"}).json()["totals"]

    assert totals == {"count": 0, "groups": []}


@pytest.mark.parametrize(
    "params",
    [
        {"side": "HOLD"},
        {"status": "DONE"},
        {"from": "yesterday"},
        {"to": "2026-13-01"},
        {"from": "2026-09-03", "to": "2026-09-02"},
        {"asset": "US DT"},
        {"asset": "<b>"},
        {"asset": "A" * 21},
    ],
)
def test_orders_reject_invalid_filters(params):
    client = TestClient(make_app(sample_store()))

    assert client.get("/api/orders", params=params).status_code == 422


def test_orders_without_a_database_are_empty():
    body = TestClient(make_app()).get("/api/orders").json()

    assert body["orders"] == [] and body["assets"] == []
    assert body["totals"] == {"count": 0, "groups": []}
    assert body["sync"] == {
        "configured": False,
        "running": False,
        "interval_seconds": None,
        "last_attempt": None,
        "last_success": None,
        "last_error": None,
    }


# ---------- background sync


class FakeSync:
    """Blocking sync callable, as the web app runs it in a thread."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.release = threading.Event()
        self.release.set()

    def __call__(self) -> None:
        self.calls += 1
        self.release.wait(5)
        if self.error is not None:
            raise self.error


def fixed_now() -> datetime:
    return datetime(2026, 10, 21, 15, 0, tzinfo=UTC)


def run_once(runner: OrderSyncRunner) -> None:
    async def scenario() -> None:
        assert runner.trigger()
        await runner.wait()

    asyncio.run(scenario())


def test_successful_sync_records_the_time_and_clears_the_error():
    sync = FakeSync(error=OrderHistoryError("Binance API error -1: busy"))
    runner = OrderSyncRunner(sync, interval=60, now=fixed_now)
    run_once(runner)
    assert runner.last_error == "Binance API error -1: busy"
    assert runner.last_success is None

    sync.error = None
    run_once(runner)

    assert runner.last_success == fixed_now()
    assert runner.last_attempt == fixed_now()
    assert runner.last_error is None
    assert sync.calls == 2


def test_sync_errors_never_leak_more_than_a_short_safe_message():
    # Validation messages quote the offending input (e.g. a counterparty nick).
    payload_error = OrderHistoryError(
        "Unexpected Binance order history payload: 1 validation error\n"
        "data.0.amount input_value='secret-nick'"
    )
    assert safe_error(payload_error) == (
        "Unexpected Binance order history payload: 1 validation error"
    )
    assert safe_error(RuntimeError("https://api/?signature=abc")) == "RuntimeError"
    assert len(safe_error(OrderHistoryError("x" * 1000))) <= 200


def test_unexpected_sync_error_is_recorded_by_class_name_only(caplog):
    runner = OrderSyncRunner(FakeSync(error=RuntimeError("signature=abc")), interval=1)

    run_once(runner)

    assert runner.last_error == "RuntimeError"
    assert "signature=abc" not in caplog.text


def test_two_syncs_never_overlap():
    sync = FakeSync()
    sync.release.clear()
    runner = OrderSyncRunner(sync, interval=60)

    async def scenario() -> tuple[bool, bool, bool]:
        first = runner.trigger()
        await asyncio.sleep(0.05)
        second = runner.trigger()
        running = runner.running
        sync.release.set()
        await runner.wait()
        return first, second, running

    assert asyncio.run(scenario()) == (True, False, True)
    assert sync.calls == 1
    assert not runner.running


def test_unconfigured_runner_never_syncs():
    runner = OrderSyncRunner(None, interval=60)

    async def scenario() -> bool:
        await runner.start()
        started = runner.trigger()
        await runner.stop()
        return started

    assert asyncio.run(scenario()) is False
    assert not runner.configured


def test_runner_syncs_at_start_and_then_on_its_interval():
    sync = FakeSync()
    runner = OrderSyncRunner(sync, interval=0.01)

    async def scenario() -> None:
        await runner.start()
        async with asyncio.timeout(5):
            while sync.calls < 3:
                await asyncio.sleep(0.01)
        await runner.stop()

    asyncio.run(scenario())
    assert runner.last_success is not None


def test_app_runs_one_sync_at_startup_and_reports_its_status():
    sync = FakeSync()
    runner = OrderSyncRunner(sync, interval=3600, now=fixed_now)
    app = make_app(sample_store(), runner)

    with TestClient(app) as client:
        client.portal.call(runner.wait)
        status = client.get("/api/orders/sync").json()

    assert sync.calls == 1
    assert status == {
        "configured": True,
        "running": False,
        "interval_seconds": 3600,
        "last_attempt": "2026-10-21T15:00:00+00:00",
        "last_success": "2026-10-21T15:00:00+00:00",
        "last_error": None,
    }


def test_interval_zero_disables_the_automatic_sync_but_not_sync_now():
    sync = FakeSync()
    runner = OrderSyncRunner(sync, interval=0)
    app = make_app(sample_store(), runner)

    with TestClient(app) as client:
        client.portal.call(runner.wait)
        assert sync.calls == 0
        response = client.post("/api/orders/sync")
        client.portal.call(runner.wait)

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert sync.calls == 1


def test_a_failing_sync_never_breaks_the_monitor():
    runner = OrderSyncRunner(FakeSync(error=RuntimeError("boom")), interval=3600)
    app = make_app(sample_store(), runner)

    with TestClient(app) as client:
        client.portal.call(runner.wait)
        snapshots = client.get("/api/snapshots")
        orders = client.get("/api/orders")

    assert snapshots.status_code == 200
    assert orders.status_code == 200
    assert orders.json()["sync"]["last_error"] == "RuntimeError"


def test_sync_now_without_credentials_is_a_conflict():
    response = TestClient(make_app(sample_store())).post("/api/orders/sync")

    assert response.status_code == 409


# ---------- p2p-web wiring


def test_web_flags_add_sync_interval_db_and_env_file():
    options = parse_args(["--sync-interval", "0", "--db", "x.sqlite"])

    assert options.sync_interval == 0
    assert options.db == "x.sqlite"
    assert parse_args([]).sync_interval == 5
    with pytest.raises(SystemExit):
        parse_args(["--sync-interval", "-1"])


def test_no_credentials_and_no_database_means_no_store_and_no_sync(tmp_path):
    store, runner = build_order_history(
        env_file=tmp_path / "missing.env",
        db=str(tmp_path / "orders.sqlite"),
        sync_interval_minutes=5,
        environ={},
    )

    assert store is None
    assert not runner.configured
    assert not (tmp_path / "orders.sqlite").exists()  # never created


def test_existing_database_is_read_without_credentials(tmp_path):
    db = tmp_path / "orders.sqlite"
    SqliteOrderStore(db).close()

    store, runner = build_order_history(
        env_file=tmp_path / "missing.env",
        db=None,
        sync_interval_minutes=5,
        environ={"P2P_DB_PATH": str(db)},
    )

    assert store is not None and not runner.configured
    store.close()


def test_credentials_configure_the_sync_with_the_interval_in_minutes(tmp_path):
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    received = []

    def make_sync(credentials, store):
        received.append(credentials)
        return FakeSync()

    store, runner = build_order_history(
        env_file=tmp_path / "missing.env",
        db=str(tmp_path / "orders.sqlite"),
        sync_interval_minutes=2.5,
        environ=CREDENTIALS_ENV,
        make_sync=make_sync,
    )

    assert runner.configured and runner.interval == 150
    assert received[0].api_key == "test-key"
    assert store is not None
    store.close()
    # httpx logs every request URL at INFO, and signed URLs carry the signature.
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


# ---------- page


def test_page_has_a_history_tab_rendered_with_text_content_only():
    client = TestClient(make_app())

    page = client.get("/").text
    script = client.get("/app.js").text

    assert 'id="tab-history"' in page and "Sync now" in page
    assert "Order history sync" in page  # README pointer when not configured
    assert "/api/orders" in script and "/api/orders/sync" in script
    assert "innerHTML" not in script


# ---------- real maker fee for the calculator


def test_maker_fee_is_the_effective_rate_of_completed_maker_orders():
    store = SqliteOrderStore(":memory:")
    created = datetime(2026, 9, 1, tzinfo=UTC)
    store.upsert(
        [
            make_order("1", created=created, amount="166.11", commission="0.33"),
            make_order("2", created=created, amount="2010.94", commission="4.02"),
            # ignored: cancelled (never charged) and taker orders
            make_order(
                "3",
                created=created,
                status=OrderStatus.CANCELLED,
                amount="10",
                commission="5",
            ),
            make_order(
                "4",
                created=created,
                role=OrderRole.TAKER,
                amount="10",
                commission=None,
            ),
        ]
    )
    client = TestClient(make_app(store))

    assert client.get("/api/orders/maker-fee").json() == {"maker_fee_pct": "0.20"}


def test_maker_fee_is_null_without_maker_orders_or_database():
    store = SqliteOrderStore(":memory:")
    store.upsert(
        [
            make_order(
                "1",
                created=datetime(2026, 9, 1, tzinfo=UTC),
                role=OrderRole.TAKER,
                commission=None,
            )
        ]
    )

    with_takers = TestClient(make_app(store)).get("/api/orders/maker-fee").json()
    no_database = TestClient(make_app()).get("/api/orders/maker-fee").json()

    assert with_takers == {"maker_fee_pct": None}
    assert no_database == {"maker_fee_pct": None}


def test_calculator_defaults_to_the_real_fee_unless_the_user_set_one():
    client = TestClient(make_app())

    page = client.get("/").text
    script = client.get("/app.js").text

    assert 'id="fee-hint"' in page and "from your orders" in page
    assert "/api/orders/maker-fee" in script
    # the real rate never replaces a stored (user-entered) value
    assert "readStoredFee() !== null" in script
