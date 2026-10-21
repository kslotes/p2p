"""SQLite order store (stdlib ``sqlite3``).

Money is stored as TEXT so ``Decimal`` values round-trip exactly, and
``created_at`` as integer epoch milliseconds (UTC) for indexing and sorting.
Rows are keyed by order number; an upsert overwrites every column, so a
re-sync is idempotent and status changes on Binance land locally.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

from p2p.domain.orders import (
    FINAL_STATUSES,
    Order,
    OrderRole,
    OrderSide,
    OrderStatus,
)

MEMORY = ":memory:"
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_number     TEXT PRIMARY KEY,
    adv_no           TEXT NOT NULL,
    side             TEXT NOT NULL,
    asset            TEXT NOT NULL,
    fiat             TEXT NOT NULL,
    fiat_symbol      TEXT NOT NULL,
    amount           TEXT NOT NULL,
    total_price      TEXT NOT NULL,
    unit_price       TEXT NOT NULL,
    status           TEXT NOT NULL,
    created_at       INTEGER NOT NULL,
    role             TEXT NOT NULL,
    commission       TEXT,
    taker_commission TEXT,
    counterparty     TEXT NOT NULL,
    pay_method       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_created_at ON orders (created_at);
"""

_COLUMNS = (
    "order_number",
    "adv_no",
    "side",
    "asset",
    "fiat",
    "fiat_symbol",
    "amount",
    "total_price",
    "unit_price",
    "status",
    "created_at",
    "role",
    "commission",
    "taker_commission",
    "counterparty",
    "pay_method",
)

_UPSERT = (
    f"INSERT INTO orders ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _COLUMNS)}) "
    "ON CONFLICT(order_number) DO UPDATE SET "
    + ", ".join(f"{column} = excluded.{column}" for column in _COLUMNS[1:])
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _to_ms(moment: datetime) -> int:
    # Integer arithmetic on the timedelta: no float rounding of milliseconds.
    return (moment - _EPOCH) // timedelta(milliseconds=1)


def _from_ms(value: int) -> datetime:
    return _EPOCH + timedelta(milliseconds=value)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


class SqliteOrderStore:
    """``OrderStore`` over one SQLite file (or ``":memory:"``).

    The connection may be shared across threads; a lock serializes access.
    """

    def __init__(self, path: str | Path) -> None:
        if str(path) != MEMORY:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        with self._lock, self._connection:
            self._connection.executescript(_SCHEMA)
            self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def upsert(self, orders: Iterable[Order]) -> int:
        rows = [self._row(order) for order in orders]
        if rows:
            with self._lock, self._connection:
                self._connection.executemany(_UPSERT, rows)
        return len(rows)

    def query(
        self,
        *,
        asset: str | None = None,
        side: OrderSide | None = None,
        status: OrderStatus | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Order]:
        clauses: list[str] = []
        params: list[Any] = []
        for clause, value in (
            ("asset = ?", asset),
            ("side = ?", side.value if side else None),
            ("status = ?", status.value if status else None),
            ("created_at >= ?", _to_ms(start) if start else None),
            ("created_at < ?", _to_ms(end) if end else None),
        ):
            if value is not None:
                clauses.append(clause)
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT {', '.join(_COLUMNS)} FROM orders{where} "
            "ORDER BY created_at DESC, order_number DESC"
        )
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._order(row) for row in rows]

    def latest_created_at(self) -> datetime | None:
        return self._moment("SELECT MAX(created_at) FROM orders", ())

    def oldest_open_created_at(self) -> datetime | None:
        final = sorted(status.value for status in FINAL_STATUSES)
        placeholders = ", ".join("?" for _ in final)
        return self._moment(
            f"SELECT MIN(created_at) FROM orders WHERE status NOT IN ({placeholders})",
            final,
        )

    def _moment(self, sql: str, params: Iterable[Any]) -> datetime | None:
        with self._lock:
            (value,) = self._connection.execute(sql, tuple(params)).fetchone()
        return None if value is None else _from_ms(value)

    @staticmethod
    def _row(order: Order) -> tuple[Any, ...]:
        return (
            order.order_number,
            order.adv_no,
            order.side.value,
            order.asset,
            order.fiat,
            order.fiat_symbol,
            str(order.amount),
            str(order.total_price),
            str(order.unit_price),
            order.status.value,
            _to_ms(order.created_at),
            order.role.value,
            _text(order.commission),
            _text(order.taker_commission),
            order.counterparty,
            order.pay_method,
        )

    @staticmethod
    def _order(row: tuple[Any, ...]) -> Order:
        (
            order_number,
            adv_no,
            side,
            asset,
            fiat,
            fiat_symbol,
            amount,
            total_price,
            unit_price,
            status,
            created_at,
            role,
            commission,
            taker_commission,
            counterparty,
            pay_method,
        ) = row
        return Order(
            order_number=order_number,
            adv_no=adv_no,
            side=OrderSide(side),
            asset=asset,
            fiat=fiat,
            fiat_symbol=fiat_symbol,
            amount=Decimal(amount),
            total_price=Decimal(total_price),
            unit_price=Decimal(unit_price),
            status=OrderStatus.parse(status),
            created_at=_from_ms(created_at),
            role=OrderRole(role),
            commission=_decimal(commission),
            taker_commission=_decimal(taker_commission),
            counterparty=counterparty,
            pay_method=pay_method,
        )
