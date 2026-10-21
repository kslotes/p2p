"""``p2p-sync``: pull the Binance P2P order history into the local database.

Needs a read-only Binance API key in ``BINANCE_API_KEY`` /
``BINANCE_API_SECRET`` (environment or ``.env``). The key and secret are
never printed or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

import httpx
from rich.console import Console
from rich.text import Text

from p2p.adapters.binance_c2c_history import BinanceC2COrderHistory
from p2p.adapters.sqlite_order_store import SqliteOrderStore
from p2p.application.order_sync import SyncOrderHistory, SyncResult
from p2p.application.ports import OrderHistorySource
from p2p.config import (
    API_KEY_VAR,
    API_SECRET_VAR,
    DB_PATH_VAR,
    DEFAULT_DB_PATH,
    ENV_FILE_NAME,
    Credentials,
    credentials_from,
    db_path_from,
    load_env,
)
from p2p.domain.orders import OrderHistoryError

EXIT_OK, EXIT_SYNC_ERROR, EXIT_NO_CREDENTIALS = 0, 1, 2
TIMEOUT_SECONDS = 20
# httpx logs every request URL at INFO, and signed URLs carry the signature.
QUIET_LOGGERS = ("httpx", "httpcore")

SourceFactory = Callable[[httpx.AsyncClient, Credentials], OrderHistorySource]


def _binance_source(
    client: httpx.AsyncClient, credentials: Credentials
) -> OrderHistorySource:
    return BinanceC2COrderHistory(
        client, api_key=credentials.api_key, api_secret=credentials.api_secret
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="p2p-sync",
        description="Sync your Binance P2P order history into a local SQLite "
        "database (read-only API key required).",
    )
    parser.add_argument(
        "--db",
        help=f"SQLite database path "
        f"(default: ${DB_PATH_VAR} or {DEFAULT_DB_PATH.as_posix()}).",
    )
    parser.add_argument(
        "--env-file",
        help=f"File with {API_KEY_VAR} / {API_SECRET_VAR} "
        f"(default: {ENV_FILE_NAME} in the current directory).",
    )
    return parser.parse_args(argv)


def _quiet_http_loggers() -> None:
    for name in QUIET_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(max(logger.getEffectiveLevel(), logging.WARNING))


def _missing_credentials(env_file: Path) -> Text:
    return Text(
        "Binance API credentials not found.\n\n"
        "1. In Binance: Account > API Management > Create API (system generated).\n"
        "2. Leave ONLY 'Enable Reading' checked: no trading, no withdrawals.\n"
        "3. Restrict access to trusted IPs and add your public IP. Home IPs can\n"
        "   change: when the sync reports an API key / IP error, update the IP\n"
        "   in Binance.\n"
        f"4. Put the key in {env_file} (never commit it) or in the environment:\n\n"
        f"   {API_KEY_VAR}=your-api-key\n"
        f"   {API_SECRET_VAR}=your-api-secret\n",
    )


def _summary(
    result: SyncResult, total: int, newest: datetime | None, db_path: Path
) -> Text:
    newest_text = newest.strftime("%Y-%m-%d %H:%M UTC") if newest else "-"
    text = Text("Order history synced", style="bold green")
    text.append(f" ({result.start:%Y-%m-%d} to {result.end:%Y-%m-%d})\n", style="green")
    text.append(
        f"Windows: {result.windows}   Fetched: {result.fetched}   "
        f"Upserted: {result.upserted}\n"
        f"Total stored: {total}   Newest order: {newest_text}\n"
        f"Database: {db_path}"
    )
    return text


async def _sync(
    credentials: Credentials,
    db_path: Path,
    console: Console,
    make_source: SourceFactory,
) -> int:
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        with SqliteOrderStore(db_path) as store:
            use_case = SyncOrderHistory(make_source(client, credentials), store)
            try:
                result = await use_case.execute()
            except OrderHistoryError as exc:
                # Text, not markup: the message may contain square brackets.
                console.print(Text(f"Sync failed: {exc}", style="bold red"))
                return EXIT_SYNC_ERROR
            total = len(store.query())
            newest = store.latest_created_at()
    console.print(_summary(result, total, newest, db_path))
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    console: Console | None = None,
    make_source: SourceFactory = _binance_source,
) -> int:
    args = parse_args(argv)
    _quiet_http_loggers()
    if console is None:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        console = Console()

    env_file = Path(args.env_file) if args.env_file else Path.cwd() / ENV_FILE_NAME
    env = load_env(env_file, environ)
    credentials = credentials_from(env)
    if credentials is None:
        console.print(_missing_credentials(env_file))
        return EXIT_NO_CREDENTIALS

    db_path = db_path_from(env, args.db)
    try:
        return asyncio.run(_sync(credentials, db_path, console, make_source))
    except KeyboardInterrupt:
        console.print("Stopped; orders synced so far are kept.")
        return EXIT_SYNC_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
