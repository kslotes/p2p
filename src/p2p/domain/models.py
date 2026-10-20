"""Pure domain models for P2P market data. No I/O lives here."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    """The side of the *advertiser* who published an ad.

    Binance's search API is phrased from the taker's point of view: asking for
    ``tradeType="BUY"`` ("I want to buy") returns ads whose advertiser SELLS.
    The domain always speaks about the advertiser side; use ``counterpart`` to
    get the taker intent that matches an ad.
    """

    BUY = "BUY"
    SELL = "SELL"

    @property
    def counterpart(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


MERCHANT = "merchant"


@dataclass(frozen=True, slots=True)
class Advertiser:
    nick: str
    month_orders: int
    finish_rate: Decimal  # ratio in [0, 1]
    user_type: str | None = None  # "merchant" or "user" as Binance reports it
    merchant_identity: str | None = None  # e.g. MASS_MERCHANT, BLOCK_MERCHANT

    @property
    def verified(self) -> bool:
        """Binance verified merchant; a missing user type counts as not verified."""
        return self.user_type == MERCHANT


@dataclass(frozen=True, slots=True)
class Ad:
    ad_id: str
    side: Side  # advertiser side
    asset: str
    fiat: str
    price: Decimal
    available: Decimal  # asset amount still tradable
    min_amount: Decimal  # fiat limits per order
    max_amount: Decimal
    payment_methods: tuple[str, ...]
    advertiser: Advertiser


class MarketDataError(Exception):
    """Market data could not be obtained (network, HTTP or API error)."""


@dataclass(frozen=True, slots=True)
class Spread:
    """Best ask minus best bid; ``percentage`` is relative to the best bid."""

    absolute: Decimal
    percentage: Decimal


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Order book view for one asset/fiat pair.

    ``asks`` are ads from advertisers who SELL (a taker buys from them), sorted
    cheapest first. ``bids`` are ads from advertisers who BUY (a taker sells to
    them), sorted highest first.
    """

    asset: str
    fiat: str
    asks: tuple[Ad, ...]
    bids: tuple[Ad, ...]

    @classmethod
    def from_ads(
        cls, *, asset: str, fiat: str, asks: Iterable[Ad], bids: Iterable[Ad]
    ) -> MarketSnapshot:
        asks, bids = tuple(asks), tuple(bids)
        if any(ad.side is not Side.SELL for ad in asks):
            raise ValueError("asks must only contain SELL ads")
        if any(ad.side is not Side.BUY for ad in bids):
            raise ValueError("bids must only contain BUY ads")
        return cls(
            asset=asset,
            fiat=fiat,
            asks=tuple(sorted(asks, key=lambda ad: ad.price)),
            bids=tuple(sorted(bids, key=lambda ad: ad.price, reverse=True)),
        )

    @property
    def best_ask(self) -> Ad | None:
        """Cheapest price a taker can buy at."""
        return self.asks[0] if self.asks else None

    @property
    def best_bid(self) -> Ad | None:
        """Highest price a taker can sell at."""
        return self.bids[0] if self.bids else None

    @property
    def spread(self) -> Spread | None:
        ask, bid = self.best_ask, self.best_bid
        if ask is None or bid is None or bid.price == 0:
            return None
        absolute = ask.price - bid.price
        return Spread(absolute=absolute, percentage=absolute / bid.price * 100)
