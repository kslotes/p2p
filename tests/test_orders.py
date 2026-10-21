from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from p2p.domain.orders import (
    Order,
    OrderRole,
    OrderSide,
    OrderStatus,
    effective_maker_fee_rate,
    summarize,
)

CREATED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def make_order(
    number: str = "1",
    *,
    side: OrderSide = OrderSide.SELL,
    status: OrderStatus = OrderStatus.COMPLETED,
    role: OrderRole = OrderRole.MAKER,
    amount: str = "100",
    total_price: str = "150000",
    commission: str | None = "0.2",
    taker_commission: str | None = None,
    asset: str = "USDT",
    fiat: str = "ARS",
) -> Order:
    return Order(
        order_number=number,
        adv_no="adv-1",
        side=side,
        asset=asset,
        fiat=fiat,
        fiat_symbol="$",
        amount=Decimal(amount),
        total_price=Decimal(total_price),
        unit_price=Decimal(total_price) / Decimal(amount),
        status=status,
        created_at=CREATED,
        role=role,
        commission=None if commission is None else Decimal(commission),
        taker_commission=None
        if taker_commission is None
        else Decimal(taker_commission),
        counterparty="buyer-x",
        pay_method="Bank",
    )


@pytest.mark.parametrize(
    ("status", "final"),
    [
        (OrderStatus.PENDING, False),
        (OrderStatus.TRADING, False),
        (OrderStatus.BUYER_PAYED, False),
        (OrderStatus.DISTRIBUTING, False),
        (OrderStatus.IN_APPEAL, False),
        (OrderStatus.UNKNOWN, False),
        (OrderStatus.COMPLETED, True),
        (OrderStatus.CANCELLED, True),
        (OrderStatus.CANCELLED_BY_SYSTEM, True),
    ],
)
def test_only_completed_and_cancelled_orders_are_final(status, final):
    assert make_order(status=status).is_final is final


def test_known_statuses_parse_and_unknown_ones_map_to_unknown():
    assert OrderStatus.parse("COMPLETED") is OrderStatus.COMPLETED
    assert OrderStatus.parse("BUYER_PAYED") is OrderStatus.BUYER_PAYED
    assert OrderStatus.parse("SOMETHING_NEW") is OrderStatus.UNKNOWN
    assert OrderStatus.parse("") is OrderStatus.UNKNOWN


def test_order_rejects_a_naive_created_at():
    with pytest.raises(ValueError, match="timezone"):
        replace(make_order(), created_at=datetime(2026, 9, 1, 12, 0))


def test_order_normalizes_created_at_to_utc():
    local = datetime(2026, 9, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
    order = replace(make_order(), created_at=local)

    assert order.created_at == CREATED
    assert order.created_at.tzinfo is UTC


def test_fee_is_the_commission_of_the_order_role():
    maker = make_order(role=OrderRole.MAKER, commission="0.33")
    taker = make_order(role=OrderRole.TAKER, commission=None, taker_commission="0.1")
    free_taker = make_order(role=OrderRole.TAKER, commission=None)

    assert maker.fee == Decimal("0.33")
    assert taker.fee == Decimal("0.1")
    assert free_taker.fee == Decimal(0)


def test_summary_only_counts_completed_orders_grouped_by_side_asset_and_fiat():
    orders = [
        make_order("1", amount="100", total_price="150000", commission="0.2"),
        make_order("2", amount="50", total_price="76000", commission="0.1"),
        make_order("3", status=OrderStatus.CANCELLED, commission="9"),
        make_order("4", status=OrderStatus.TRADING),
        make_order(
            "5",
            side=OrderSide.BUY,
            role=OrderRole.TAKER,
            amount="10",
            total_price="15500",
            commission=None,
        ),
    ]

    summary = summarize(orders)

    assert summary.count == 3
    assert summary.fees == Decimal("0.3")
    sell = summary.group(OrderSide.SELL, "USDT", "ARS")
    buy = summary.group(OrderSide.BUY, "USDT", "ARS")
    assert buy is not None and sell is not None
    assert (buy.count, buy.amount, buy.total_price, buy.fees) == (
        1,
        Decimal(10),
        Decimal(15500),
        Decimal(0),
    )
    assert (sell.count, sell.amount, sell.total_price, sell.fees) == (
        2,
        Decimal(150),
        Decimal(226000),
        Decimal("0.3"),
    )


def test_summary_keeps_different_fiats_apart():
    summary = summarize([make_order("1", fiat="ARS"), make_order("2", fiat="USD")])

    assert len(summary.groups) == 2
    assert summary.group(OrderSide.SELL, "USDT", "USD") is not None
    assert summary.group(OrderSide.BUY, "USDT", "USD") is None


def test_summary_of_no_orders_is_empty():
    summary = summarize([])

    assert (summary.count, summary.fees, summary.groups) == (0, Decimal(0), ())


def test_effective_maker_fee_rate_uses_completed_maker_orders_only():
    orders = [
        make_order("1", amount="166.11", commission="0.33"),
        make_order("2", amount="2010.94", commission="4.02"),
        make_order("3", status=OrderStatus.CANCELLED, amount="999", commission="5"),
        make_order("4", role=OrderRole.TAKER, amount="500", commission=None),
        make_order("5", amount="70", commission=None),  # no data: ignored
    ]

    rate = effective_maker_fee_rate(orders)

    assert rate == Decimal("4.35") / Decimal("2177.05")
    assert rate is not None and round(rate, 4) == Decimal("0.0020")


def test_effective_maker_fee_rate_is_none_without_completed_maker_orders():
    assert effective_maker_fee_rate([]) is None
    assert effective_maker_fee_rate([make_order(status=OrderStatus.CANCELLED)]) is None
    assert effective_maker_fee_rate([make_order(role=OrderRole.TAKER)]) is None
