from decimal import Decimal

import pytest

from p2p.domain import Outcome, estimate, round_trip
from p2p.domain.fees import MAX_NOTIONAL, MAX_PRICE


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


def test_round_trip_accepts_prices_up_to_the_documented_maximum():
    result = round_trip(sell=MAX_PRICE, rebuy=MAX_PRICE, fee_rate=Decimal("0.002"))

    assert result.outcome is Outcome.LOSS


@pytest.mark.parametrize(
    ("sell", "rebuy"),
    [("1E+50", "1610"), ("1618", "1E+50"), ("1000000000.01", "1610")],
)
def test_round_trip_rejects_prices_above_the_maximum(sell, rebuy):
    with pytest.raises(ValueError, match="at most"):
        round_trip(sell=Decimal(sell), rebuy=Decimal(rebuy), fee_rate=Decimal("0.002"))


@pytest.mark.parametrize(("sell", "rebuy"), [("0.001", "0.001"), ("0.004", "1")])
def test_round_trip_rejects_prices_whose_real_leg_rounds_to_zero(sell, rebuy):
    # 0.001 x 0.998 and 0.001 / 0.998 both round to 0.00 fiat
    with pytest.raises(ValueError, match="0.00"):
        round_trip(sell=Decimal(sell), rebuy=Decimal(rebuy), fee_rate=Decimal("0.002"))


def test_round_trip_rejects_a_fee_so_close_to_100_percent_that_prices_overflow():
    with pytest.raises(ValueError, match="out of range"):
        round_trip(
            sell=Decimal(1),
            rebuy=MAX_PRICE,
            fee_rate=Decimal("0.9999999999999999999"),
        )


def test_estimate_on_1000_units_of_the_example_gain():
    trip = round_trip(
        sell=Decimal(1618), rebuy=Decimal(1610), fee_rate=Decimal("0.002")
    )

    result = estimate(trip)

    assert result.notional == Decimal(1000)
    assert result.fiat_result == Decimal("1530.00")  # 1000 x 1.53
    assert result.asset_result == Decimal("0.95")  # 1530 / 1613.23 = 0.948...


def test_estimate_keeps_the_sign_of_a_loss_and_takes_a_custom_notional():
    trip = round_trip(
        sell=Decimal(1618), rebuy=Decimal(1610), fee_rate=Decimal("0.0035")
    )

    result = estimate(trip, notional=Decimal(250))

    assert result.fiat_result == Decimal("-827.50")  # 250 x -3.31
    assert result.asset_result == Decimal("-0.51")  # -827.50 / 1615.65


def test_estimate_of_a_break_even_round_trip_is_zero_without_a_sign():
    trip = round_trip(sell=Decimal(1600), rebuy=Decimal(1600), fee_rate=Decimal(0))

    result = estimate(trip)

    assert str(result.fiat_result) == "0.00"
    assert str(result.asset_result) == "0.00"


def test_estimate_asset_result_rounding_to_zero_drops_the_minus_sign():
    # 1 x -0.01 = -0.01 ARS, -0.01 / 1613 rounds to 0.00 USDT, never "-0.00"
    trip = round_trip(sell=Decimal("1612.99"), rebuy=Decimal(1613), fee_rate=Decimal(0))

    result = estimate(trip, notional=Decimal(1))

    assert result.fiat_result == Decimal("-0.01")
    assert str(result.asset_result) == "0.00"


@pytest.mark.parametrize("notional", ["0", "-1", "1000000000.01"])
def test_estimate_rejects_a_notional_outside_its_range(notional):
    trip = round_trip(
        sell=Decimal(1618), rebuy=Decimal(1610), fee_rate=Decimal("0.002")
    )

    with pytest.raises(ValueError, match="amount"):
        estimate(trip, notional=Decimal(notional))


def test_estimate_accepts_the_maximum_notional():
    trip = round_trip(sell=MAX_PRICE, rebuy=Decimal(1), fee_rate=Decimal(0))

    assert estimate(trip, notional=MAX_NOTIONAL).fiat_result > 0
