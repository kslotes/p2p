"""Implied fiat-per-USDT rate of a non-stablecoin P2P ad.

A BTC ad quotes fiat per BTC; dividing by the BTC spot price (USDT per BTC)
gives what that ad effectively charges or pays per USDT, comparable with USDT
ads at a glance. Stablecoins need no conversion.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
print('test');

CENT = Decimal("0.01")
STABLECOINS = frozenset({"USDT", "USDC", "FDUSD", "DAI", "TUSD"})


def is_stablecoin(asset: str) -> bool:
    return asset.upper() in STABLECOINS


def implied_usdt_rate(*, price: Decimal, spot: Decimal) -> Decimal:
    """Fiat per USDT: ``price`` (fiat per asset) / ``spot`` (USDT per asset).

    Rounded half up to cents. Raises ``ValueError`` unless ``spot > 0``.
    """
    if spot <= 0:
        raise ValueError("spot price must be greater than 0")
    return (price / spot).quantize(CENT, rounding=ROUND_HALF_UP)
