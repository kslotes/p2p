from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from p2p.domain import Ad, Side
from p2p.domain.orders import Order, OrderSide, OrderStatus


class MarketDataPort(Protocol):
    async def fetch_ads(
        self,
        asset: str,
        fiat: str,
        side: Side,
        rows: int,
        pay_types: tuple[str, ...] = (),
    ) -> list[Ad]:
        """Return up to ``rows`` ads published by advertisers on ``side``.

        Raises ``MarketDataError`` when data cannot be obtained.
        """
        ...


class SpotPricePort(Protocol):
    async def fetch_usdt_prices(self, assets: tuple[str, ...]) -> dict[str, Decimal]:
        """Return the spot price in USDT of every asset, keyed as given.

        Raises ``MarketDataError`` when the prices cannot be obtained.
        """
        ...


class OrderHistorySource(Protocol):
    async def fetch(
        self, side: OrderSide, start: datetime, end: datetime
    ) -> list[Order]:
        """Return every order of ``side`` created in ``[start, end]``.

        The range spans at most 30 days; the implementation handles paging.
        Raises ``OrderHistoryError`` when the history cannot be obtained.
        """
        ...


class OrderStore(Protocol):
    def upsert(self, orders: Iterable[Order]) -> int:
        """Insert or replace orders keyed by order number; return how many."""
        ...

    def query(
        self,
        *,
        asset: str | None = None,
        side: OrderSide | None = None,
        status: OrderStatus | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Order]:
        """Orders matching every given filter, newest first.

        ``start`` is inclusive and ``end`` exclusive.
        """
        ...

    def latest_created_at(self) -> datetime | None:
        """Creation time of the newest stored order, if any."""
        ...

    def oldest_open_created_at(self) -> datetime | None:
        """Creation time of the oldest stored order that is not final, if any."""
        ...
