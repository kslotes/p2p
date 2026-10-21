from decimal import Decimal

import pytest

from p2p.domain import STABLECOINS, implied_usdt_rate, is_stablecoin


def test_implied_rate_divides_the_fiat_price_by_the_usdt_spot_price():
    rate = implied_usdt_rate(price=Decimal(150_000_000), spot=Decimal(100_000))

    assert rate == Decimal("1500.00")
    assert str(rate) == "1500.00"


def test_implied_rate_rounds_half_up_to_cents():
    assert implied_usdt_rate(price=Decimal("10.005"), spot=Decimal(1)) == Decimal(
        "10.01"
    )
    assert implied_usdt_rate(price=Decimal(100), spot=Decimal(3)) == Decimal("33.33")


@pytest.mark.parametrize("spot", [Decimal(0), Decimal(-1)])
def test_implied_rate_rejects_a_spot_price_that_is_not_positive(spot):
    with pytest.raises(ValueError, match="spot"):
        implied_usdt_rate(price=Decimal(1500), spot=spot)


@pytest.mark.parametrize("asset", ["USDT", "usdc", "FDUSD", "Dai", "TUSD"])
def test_stablecoins_are_detected_case_insensitively(asset):
    assert is_stablecoin(asset)


@pytest.mark.parametrize("asset", ["BTC", "eth", "BNB"])
def test_other_assets_are_not_stablecoins(asset):
    assert not is_stablecoin(asset)


def test_stablecoin_set_is_upper_case():
    assert frozenset({"USDT", "USDC", "FDUSD", "DAI", "TUSD"}) == STABLECOINS
