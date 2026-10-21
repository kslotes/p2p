"""The user's own P2P orders and pure aggregation over them. No I/O lives here."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class OrderSide(StrEnum):
    """What the *user* did in the order: BUY (received crypto) or SELL.

    Unlike ``Side`` (the advertiser side of a public ad), this is always the
    user's own point of view, as Binance reports it in the order history.
    """

    BUY = "BUY"
    SELL = "SELL"


class OrderRole(StrEnum):
    """Whether the user published the ad (MAKER) or took it (TAKER)."""

    MAKER = "MAKER"
    TAKER = "TAKER"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    TRADING = "TRADING"
    BUYER_PAYED = "BUYER_PAYED"
    DISTRIBUTING = "DISTRIBUTING"
    COMPLETED = "COMPLETED"
    IN_APPEAL = "IN_APPEAL"
    CANCELLED = "CANCELLED"
    CANCELLED_BY_SYSTEM = "CANCELLED_BY_SYSTEM"
    # Any status Binance may add later. It is treated as non-final, so the sync
    # keeps re-checking the order, and it never counts towards totals.
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, raw: str) -> OrderStatus:
        """Map a Binance status string; unrecognized values become ``UNKNOWN``."""
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_final(self) -> bool:
        return self in FINAL_STATUSES


FINAL_STATUSES = frozenset(
    {OrderStatus.COMPLETED, OrderStatus.CANCELLED, OrderStatus.CANCELLED_BY_SYSTEM}
)


class OrderHistoryError(Exception):
    """The order history could not be obtained (network, auth, clock or API).

    Messages are meant for humans and never contain credentials.
    """


@dataclass(frozen=True, slots=True)
class Order:
    """One P2P order. ``amount`` is crypto, ``total_price`` is fiat.

    ``commission`` is the maker fee (in crypto) and ``taker_commission`` the
    taker fee; Binance leaves the one that does not apply as null. Cancelled
    orders may still carry a commission that was never charged.
    """

    order_number: str
    adv_no: str
    side: OrderSide
    asset: str
    fiat: str
    fiat_symbol: str
    amount: Decimal
    total_price: Decimal
    unit_price: Decimal
    status: OrderStatus
    created_at: datetime  # aware, normalized to UTC
    role: OrderRole
    commission: Decimal | None
    taker_commission: Decimal | None
    counterparty: str
    pay_method: str

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must carry a timezone")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))

    @property
    def is_final(self) -> bool:
        """COMPLETED or cancelled: Binance will not change this order anymore."""
        return self.status.is_final

    @property
    def fee(self) -> Decimal:
        """Fee for the user's role in the order; a missing value counts as 0."""
        value = (
            self.commission if self.role is OrderRole.MAKER else self.taker_commission
        )
        return value if value is not None else Decimal(0)


@dataclass(frozen=True, slots=True)
class OrderTotals:
    """Totals of COMPLETED orders for one side, asset and fiat."""

    side: OrderSide
    asset: str
    fiat: str
    count: int
    amount: Decimal  # crypto
    total_price: Decimal  # fiat
    fees: Decimal  # crypto


@dataclass(frozen=True, slots=True)
class OrderSummary:
    """Totals over COMPLETED orders only; other statuses are ignored."""

    count: int
    fees: Decimal
    groups: tuple[OrderTotals, ...]

    def group(self, side: OrderSide, asset: str, fiat: str) -> OrderTotals | None:
        return next(
            (
                totals
                for totals in self.groups
                if (totals.side, totals.asset, totals.fiat) == (side, asset, fiat)
            ),
            None,
        )


def summarize(orders: Iterable[Order]) -> OrderSummary:
    """Count, crypto amount, fiat total and fees of COMPLETED orders, grouped
    by side, asset and fiat (fiat totals in different currencies never mix)."""
    buckets: dict[tuple[OrderSide, str, str], list[Order]] = {}
    for order in orders:
        if order.status is OrderStatus.COMPLETED:
            key = (order.side, order.asset, order.fiat)
            buckets.setdefault(key, []).append(order)

    groups = tuple(
        OrderTotals(
            side=side,
            asset=asset,
            fiat=fiat,
            count=len(bucket),
            amount=sum((o.amount for o in bucket), Decimal(0)),
            total_price=sum((o.total_price for o in bucket), Decimal(0)),
            fees=sum((o.fee for o in bucket), Decimal(0)),
        )
        for (side, asset, fiat), bucket in sorted(buckets.items())
    )
    return OrderSummary(
        count=sum(g.count for g in groups),
        fees=sum((g.fees for g in groups), Decimal(0)),
        groups=groups,
    )


def effective_maker_fee_rate(orders: Iterable[Order]) -> Decimal | None:
    """sum(commission) / sum(amount) over COMPLETED MAKER orders.

    Makers without a commission value are skipped (no data, not a free
    order). Returns ``None`` when nothing qualifies.
    """
    fees = amount = Decimal(0)
    for order in orders:
        if (
            order.status is OrderStatus.COMPLETED
            and order.role is OrderRole.MAKER
            and order.commission is not None
        ):
            fees += order.commission
            amount += order.amount
    return fees / amount if amount else None
