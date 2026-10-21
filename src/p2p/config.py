"""Settings shared by the entrypoints.

Monitor flags (terminal and web) plus the order-history settings: a tiny
``.env`` loader for the Binance credentials and the SQLite database path.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from p2p.application.monitor import Config

DEFAULT_ASSETS = ("USDT", "USDC", "BTC", "ETH")
MAX_ROWS = 20  # Binance search endpoint caps page size at 20

ENV_FILE_NAME = ".env"
API_KEY_VAR = "BINANCE_API_KEY"
API_SECRET_VAR = "BINANCE_API_SECRET"
DB_PATH_VAR = "P2P_DB_PATH"
DEFAULT_DB_PATH = Path("data/p2p.sqlite")
_QUOTES = ('"', "'")


@dataclass(frozen=True, slots=True)
class Credentials:
    """Binance API key and secret. ``repr``/``str`` never show either value."""

    api_key: str
    api_secret: str

    def __repr__(self) -> str:
        return f"{type(self).__name__}(api_key='***', api_secret='***')"

    __str__ = __repr__


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines.

    Blank lines, ``#`` comments and lines without ``=`` are ignored; an
    optional ``export`` prefix and one pair of surrounding quotes are
    stripped. There are no inline comments or variable expansion.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] in _QUOTES and value[0] == value[-1]:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_env(
    env_file: str | Path | None = None, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """``.env`` values (default: ``./.env``) overridden by real environment ones.

    A missing file is not an error. ``utf-8-sig`` tolerates a BOM, which
    Windows editors like to add. An empty environment variable does not hide
    a value from the file.
    """
    path = Path(env_file) if env_file is not None else Path.cwd() / ENV_FILE_NAME
    values = parse_env(path.read_text("utf-8-sig")) if path.is_file() else {}
    real = os.environ if environ is None else environ
    values.update({key: value for key, value in real.items() if value})
    return values


def credentials_from(env: Mapping[str, str]) -> Credentials | None:
    """Credentials when both the key and the secret are set (non-blank)."""
    api_key = env.get(API_KEY_VAR, "").strip()
    api_secret = env.get(API_SECRET_VAR, "").strip()
    if not api_key or not api_secret:
        return None
    return Credentials(api_key, api_secret)


def db_path_from(env: Mapping[str, str], override: str | Path | None = None) -> Path:
    """Database path: explicit override, then ``P2P_DB_PATH``, then the default."""
    if override:
        return Path(override)
    if value := env.get(DB_PATH_VAR, "").strip():
        return Path(value)
    return DEFAULT_DB_PATH


def _split_values(values: Sequence[str] | None) -> tuple[str, ...]:
    """Flatten repeated/comma-separated flag values, uppercased, keeping order."""
    seen: dict[str, None] = {}
    for value in values or ():
        for part in value.split(","):
            if part := part.strip().upper():
                seen.setdefault(part, None)
    return tuple(seen)


def add_monitor_arguments(
    parser: argparse.ArgumentParser, *, default_rows: int = 5
) -> None:
    """Flags shared by every monitor entrypoint (terminal and web)."""
    parser.add_argument(
        "--asset",
        action="append",
        help=f"Asset(s) to watch; repeatable or comma separated "
        f"(default: {','.join(DEFAULT_ASSETS)}).",
    )
    parser.add_argument("--fiat", default="ARS", help="Fiat currency (default: ARS).")
    parser.add_argument(
        "--rows",
        type=int,
        default=default_rows,
        help=f"Ads shown per side (default: {default_rows}).",
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
