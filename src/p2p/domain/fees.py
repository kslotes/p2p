"""Round-trip maker fee math: sell an asset, then buy it back, both as maker.

Conservative model with the fee charged in crypto on each leg:

- real sell = sell x (1 - fee): fiat received per asset actually delivered.
- real rebuy = rebuy / (1 - fee): fiat paid per asset actually received.

Real prices are fiat amounts and are rounded to cents, so the difference is
exact at that precision and break-even means it is 0.00. Prices are capped at
``MAX_PRICE`` (far above any real P2P quote; it keeps ``Decimal`` quantizing
within context precision) and a real leg that rounds to 0.00 is rejected.

``estimate`` scales a round trip to a notional amount of the asset (1,000 by
default; USDT is roughly USD): the fiat gained or lost selling and rebuying
that amount, and the asset gained or lost when all fiat proceeds are
reinvested at the real rebuy price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

CENT = Decimal("0.01")
PERCENT_PLACES = Decimal("0.0001")
MAX_PRICE = Decimal(1_000_000_000)
DEFAULT_NOTIONAL = Decimal(1000)
MAX_NOTIONAL = Decimal(1_000_000_000)


class Outcome(StrEnum):
    LOSS = "LOSS"
    BREAK_EVEN = "BREAK_EVEN"
    GAIN = "GAIN"


@dataclass(frozen=True, slots=True)
class RoundTrip:
    """Result of a sell/rebuy pair after maker fees.

    ``difference_pct`` is a percentage relative to ``real_rebuy``.
    """

    real_sell: Decimal
    real_rebuy: Decimal
    difference: Decimal
    difference_pct: Decimal
    outcome: Outcome


def round_trip(*, sell: Decimal, rebuy: Decimal, fee_rate: Decimal) -> RoundTrip:
    """Evaluate selling at ``sell`` and rebuying at ``rebuy``.

    ``fee_rate`` is a fraction (0.002 = 0.20%). Raises ``ValueError`` unless
    both prices are in ``(0, MAX_PRICE]``, ``0 <= fee_rate < 1`` and both real
    prices are representable in cents and do not round to 0.00.
    """
    if sell <= 0 or rebuy <= 0:
        raise ValueError("prices must be greater than 0")
    if sell > MAX_PRICE or rebuy > MAX_PRICE:
        raise ValueError(f"prices must be at most {MAX_PRICE:,}")
    if not 0 <= fee_rate < 1:
        raise ValueError("fee rate must be in [0, 1)")

    keep = 1 - fee_rate
    try:
        real_sell = (sell * keep).quantize(CENT, rounding=ROUND_HALF_UP)
        real_rebuy = (rebuy / keep).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:  # a fee near 100% blows rebuy / keep up
        raise ValueError("real prices are out of range; lower the fee") from exc
    if real_sell == 0 or real_rebuy == 0:
        raise ValueError("real prices round to 0.00; prices are too small")
    difference = real_sell - real_rebuy
    difference_pct = (difference / real_rebuy * 100).quantize(
        PERCENT_PLACES, rounding=ROUND_HALF_UP
    )
    if difference > 0:
        outcome = Outcome.GAIN
    elif difference < 0:
        outcome = Outcome.LOSS
    else:
        outcome = Outcome.BREAK_EVEN
    return RoundTrip(
        real_sell=real_sell,
        real_rebuy=real_rebuy,
        difference=difference,
        difference_pct=difference_pct,
        outcome=outcome,
    )


@dataclass(frozen=True, slots=True)
class Estimate:
    """A round trip scaled to ``notional`` units of the asset.

    ``fiat_result`` = notional x difference; ``asset_result`` = fiat_result /
    real rebuy. Both are rounded to 2 decimals and share the outcome's sign.
    """

    notional: Decimal
    fiat_result: Decimal
    asset_result: Decimal


def estimate(trip: RoundTrip, notional: Decimal = DEFAULT_NOTIONAL) -> Estimate:
    """Raises ``ValueError`` unless ``notional`` is in ``(0, MAX_NOTIONAL]``."""
    if not 0 < notional <= MAX_NOTIONAL:
        raise ValueError(f"amount must be greater than 0 and at most {MAX_NOTIONAL:,}")
    fiat_result = notional * trip.difference
    asset_result = fiat_result / trip.real_rebuy
    return Estimate(
        notional=notional,
        fiat_result=_cents(fiat_result),
        asset_result=_cents(asset_result),
    )


def fee_percent(rate: Decimal) -> Decimal:
    """A fee rate as the calculator's percent input: 0.0019866 -> 0.20."""
    return _cents(rate * 100)


def _cents(value: Decimal) -> Decimal:
    rounded = value.quantize(CENT, rounding=ROUND_HALF_UP)
    return rounded if rounded else abs(rounded)  # "0.00", never "-0.00"
