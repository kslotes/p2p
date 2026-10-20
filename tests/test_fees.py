from decimal import Decimal

import pytest

from p2p.domain import Outcome, round_trip


def test_round_trip_example_gains_at_the_usdt_maker_fee():
    result = round_trip(
        sell=Decimal(1618), rebuy=Decimal(1610), fee_rate=Decimal("0.002")
    )

    assert result.real_sell == Decimal("1614.76")  # 1618 x 0.998
    assert result.real_rebuy == Decimal("1613.23")  # 1610 / 0.998
    assert result.difference == Decimal("1.53")
    assert result.difference_pct == Decimal("0.0948")  # 1.53 / 1613.23
    assert result.outcome is Outcome.GAIN


def test_round_trip_example_loses_at_a_higher_fee():
    result = round_trip(
        sell=Decimal(1618), rebuy=Decimal(1610), fee_rate=Decimal("0.0035")
    )

    assert result.real_sell == Decimal("1612.34")
    assert result.real_rebuy == Decimal("1615.65")
    assert result.difference == Decimal("-3.31")
    assert result.outcome is Outcome.LOSS


def test_round_trip_without_fee_keeps_prices():
    result = round_trip(sell=Decimal(1600), rebuy=Decimal(1600), fee_rate=Decimal(0))

    assert result.real_sell == result.real_rebuy == Decimal("1600.00")
    assert result.difference == Decimal("0.00")
    assert result.difference_pct == Decimal("0.0000")
    assert result.outcome is Outcome.BREAK_EVEN


def test_round_trip_is_break_even_when_the_difference_rounds_to_zero():
    # 1000 x 0.998 = 998 and 996.004 / 0.998 = 998: the round trip nets nothing
    result = round_trip(
        sell=Decimal(1000), rebuy=Decimal("996.004"), fee_rate=Decimal("0.002")
    )

    assert result.real_sell == Decimal("998.00")
    assert result.real_rebuy == Decimal("998.00")
    assert result.outcome is Outcome.BREAK_EVEN


@pytest.mark.parametrize(
    ("sell", "rebuy", "fee_rate"),
    [
        ("0", "1", "0.002"),
        ("1", "0", "0.002"),
        ("-1", "1", "0.002"),
        ("1", "1", "-0.001"),
        ("1", "1", "1"),
        ("1", "1", "1.5"),
    ],
)
def test_round_trip_rejects_invalid_inputs(sell, rebuy, fee_rate):
    with pytest.raises(ValueError):
        round_trip(sell=Decimal(sell), rebuy=Decimal(rebuy), fee_rate=Decimal(fee_rate))
