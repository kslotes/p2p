import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from rich.console import Console

from p2p.adapters.sqlite_order_store import SqliteOrderStore
from p2p.domain.orders import (
    Order,
    OrderHistoryError,
    OrderRole,
    OrderSide,
    OrderStatus,
)
from p2p.sync_cli import main

KEY = "test-api-key-abc"
SECRET = "test-api-secret-xyz"
CREDENTIALS_ENV = {"BINANCE_API_KEY": KEY, "BINANCE_API_SECRET": SECRET}
CREATED = (datetime.now(UTC) - timedelta(days=2)).replace(microsecond=0)


def make_order(number: str, side: OrderSide) -> Order:
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
        status=OrderStatus.COMPLETED,
        created_at=CREATED,
        role=OrderRole.MAKER,
        commission=Decimal("0.02"),
        taker_commission=None,
        counterparty="someone",
        pay_method="Bank",
    )


class FakeSource:
    def __init__(self, orders=(), error: Exception | None = None) -> None:
        self.orders = list(orders)
        self.error = error

    async def fetch(self, side, start, end):
        if self.error is not None:
            raise self.error
        return [
            o for o in self.orders if o.side is side and start <= o.created_at <= end
        ]


def run(tmp_path, source=None, environ=None, argv=()):
    console = Console(record=True, width=200)
    received = []

    def make_source(client, credentials):
        received.append(credentials)
        return source if source is not None else FakeSource()

    code = main(
        ["--env-file", str(tmp_path / "missing.env"), *argv],
        environ=CREDENTIALS_ENV if environ is None else environ,
        console=console,
        make_source=make_source,
    )
    return code, console.export_text(), received


def test_sync_prints_a_summary_and_stores_the_orders(tmp_path):
    db = tmp_path / "db" / "orders.sqlite"
    source = FakeSource(
        [make_order("1", OrderSide.BUY), make_order("2", OrderSide.SELL)]
    )

    code, text, received = run(tmp_path, source, argv=["--db", str(db)])

    assert code == 0
    assert received[0].api_key == KEY and received[0].api_secret == SECRET
    assert "Windows: 6" in text
    assert "Fetched: 2" in text
    assert "Upserted: 2" in text
    assert "Total stored: 2" in text
    assert CREATED.strftime("%Y-%m-%d") in text
    assert str(db) in text
    assert KEY not in text and SECRET not in text
    with SqliteOrderStore(db) as store:
        assert len(store.query()) == 2


def test_db_path_comes_from_the_environment_or_env_file(tmp_path):
    db = tmp_path / "from-env.sqlite"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"BINANCE_API_KEY={KEY}\nBINANCE_API_SECRET='{SECRET}'\nP2P_DB_PATH={db}\n",
        encoding="utf-8",
    )
    console = Console(record=True, width=200)

    code = main(
        ["--env-file", str(env_file)],
        environ={},
        console=console,
        make_source=lambda client, credentials: FakeSource(),
    )

    assert code == 0
    assert db.exists()


def test_missing_credentials_explain_the_setup_and_exit_2(tmp_path):
    code, text, received = run(
        tmp_path, environ={}, argv=["--db", str(tmp_path / "x.sqlite")]
    )

    assert code == 2
    assert received == []  # never reaches Binance
    assert "BINANCE_API_KEY=" in text and "BINANCE_API_SECRET=" in text
    assert "Enable Reading" in text
    assert "IP" in text
    assert "missing.env" in text


def test_order_history_error_prints_its_message_and_exits_1(tmp_path):
    source = FakeSource(error=OrderHistoryError("Binance rejected the API key [IP]"))

    code, text, _ = run(tmp_path, source, argv=["--db", str(tmp_path / "x.sqlite")])

    assert code == 1
    assert "Binance rejected the API key [IP]" in text
    assert "Total stored" not in text


def test_http_loggers_stay_quiet_so_signed_urls_are_never_logged(tmp_path):
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.DEBUG)

    run(tmp_path, argv=["--db", str(tmp_path / "x.sqlite")])

    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING


def test_help_lists_the_flags(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "--db" in out and "--env-file" in out
