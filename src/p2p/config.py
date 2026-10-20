"""Command-line flags shared by every monitor entrypoint (terminal and web)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from p2p.application.monitor import Config

DEFAULT_ASSETS = ("USDT", "USDC", "BTC", "ETH")
MAX_ROWS = 20  # Binance search endpoint caps page size at 20


def _split_values(values: Sequence[str] | None) -> tuple[str, ...]:
    """Flatten repeated/comma-separated flag values, uppercased, keeping order."""
    seen: dict[str, None] = {}
    for value in values or ():
        for part in value.split(","):
            if part := part.strip().upper():
                seen.setdefault(part, None)
    return tuple(seen)


def add_monitor_arguments(parser: argparse.ArgumentParser) -> None:
    """Flags shared by every monitor entrypoint (terminal and web)."""
    parser.add_argument(
        "--asset",
        action="append",
        help=f"Asset(s) to watch; repeatable or comma separated "
        f"(default: {','.join(DEFAULT_ASSETS)}).",
    )
    parser.add_argument("--fiat", default="ARS", help="Fiat currency (default: ARS).")
    parser.add_argument(
        "--rows", type=int, default=5, help="Ads shown per side (default: 5)."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=15,
        help="Seconds between refreshes (default: 15).",
    )
    parser.add_argument(
        "--pay-type",
        action="append",
        help="Binance payment identifier filter, e.g. MercadoPagoNew; repeatable.",
    )


def config_from_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> Config:
    """Validate and normalize the shared flags; exits via ``parser.error``."""
    if not 1 <= args.rows <= MAX_ROWS:
        parser.error(f"--rows must be between 1 and {MAX_ROWS}")
    if args.interval <= 0:
        parser.error("--interval must be greater than 0")

    # Payment identifiers are case-sensitive (e.g. MercadoPagoNew): no uppercasing.
    pay_types = tuple(
        dict.fromkeys(
            part.strip()
            for value in args.pay_type or ()
            for part in value.split(",")
            if part.strip()
        )
    )
    return Config(
        assets=_split_values(args.asset) or DEFAULT_ASSETS,
        fiat=args.fiat.strip().upper(),
        rows=args.rows,
        interval=args.interval,
        pay_types=pay_types,
        once=getattr(args, "once", False),
    )
