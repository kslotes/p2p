"""Web entrypoint: serve the live monitor over HTTP (read-only, local by default).

With Binance credentials (environment or ``.env``, as for ``p2p-sync``) the
app also keeps the local order history in sync in the background.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import uvicorn

from p2p.adapters.binance_c2c_history import BinanceC2COrderHistory
from p2p.adapters.binance_public import BinancePublicMarketData
from p2p.adapters.sqlite_order_store import SqliteOrderStore
from p2p.adapters.web.app import create_app
from p2p.adapters.web.order_sync_runner import DEFAULT_SYNC_MINUTES, OrderSyncRunner
from p2p.application import GetMarketSnapshot, OrderStore, SyncOrderHistory
from p2p.application.monitor import Config
from p2p.application.order_sync import SyncResult
from p2p.config import (
    API_KEY_VAR,
    API_SECRET_VAR,
    DB_PATH_VAR,
    DEFAULT_DB_PATH,
    ENV_FILE_NAME,
    Credentials,
    add_monitor_arguments,
    config_from_args,
    credentials_from,
    db_path_from,
    load_env,
)

SYNC_TIMEOUT_SECONDS = 20
# httpx logs every request URL at INFO, and signed URLs carry the signature.
QUIET_LOGGERS = ("httpx", "httpcore")

SyncFactory = Callable[[Credentials, OrderStore], Callable[[], Any]]


class WebOptions(NamedTuple):
    config: Config
    host: str
    port: int
    sync_interval: float  # minutes; 0 disables the automatic sync
    db: str | None
    env_file: str | None


def parse_args(argv: Sequence[str] | None = None) -> WebOptions:
    parser = argparse.ArgumentParser(
        prog="p2p-web",
        description="Live Binance P2P price monitor in the browser "
        "(public data, read-only), plus your synced order history.",
    )
    add_monitor_arguments(parser)
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Bind port (default: 8000)."
    )
    parser.add_argument(
        "--sync-interval",
        type=float,
        default=DEFAULT_SYNC_MINUTES,
        help=f"Minutes between order history syncs when {API_KEY_VAR} / "
        f"{API_SECRET_VAR} are set; 0 disables the automatic sync "
        f"(default: {DEFAULT_SYNC_MINUTES:g}).",
    )
    parser.add_argument(
        "--db",
        help=f"SQLite order database "
        f"(default: ${DB_PATH_VAR} or {DEFAULT_DB_PATH.as_posix()}).",
    )
    parser.add_argument(
        "--env-file",
        help=f"File with the Binance credentials "
        f"(default: {ENV_FILE_NAME} in the current directory).",
    )
    args = parser.parse_args(argv)
    if args.sync_interval < 0:
        parser.error("--sync-interval must be 0 or greater")
    return WebOptions(
        config=config_from_args(parser, args),
        host=args.host,
        port=args.port,
        sync_interval=args.sync_interval,
        db=args.db,
        env_file=args.env_file,
    )


def _binance_sync(credentials: Credentials, store: OrderStore) -> Callable[[], Any]:
    """Blocking sync: a fresh event loop and HTTP client per run (worker thread)."""

    async def run() -> SyncResult:
        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT_SECONDS) as client:
            source = BinanceC2COrderHistory(
                client, api_key=credentials.api_key, api_secret=credentials.api_secret
            )
            return await SyncOrderHistory(source, store).execute()

    return lambda: asyncio.run(run())


def _quiet_http_loggers() -> None:
    for name in QUIET_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(max(logger.getEffectiveLevel(), logging.WARNING))


def build_order_history(
    *,
    env_file: str | Path | None,
    db: str | None,
    sync_interval_minutes: float,
    environ: Mapping[str, str] | None = None,
    make_sync: SyncFactory = _binance_sync,
) -> tuple[SqliteOrderStore | None, OrderSyncRunner]:
    """Open the order database and set up the background sync.

    Without credentials an existing database is still opened (read-only use),
    but a missing one is not created; the sync stays unconfigured.
    """
    _quiet_http_loggers()
    env = load_env(env_file, environ)
    credentials = credentials_from(env)
    db_path = db_path_from(env, db)
    store = (
        SqliteOrderStore(db_path)
        if credentials is not None or db_path.is_file()
        else None
    )
    sync = (
        make_sync(credentials, store)
        if credentials is not None and store is not None
        else None
    )
    return store, OrderSyncRunner(sync, interval=sync_interval_minutes * 60)


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)
    store, order_sync = build_order_history(
        env_file=options.env_file,
        db=options.db,
        sync_interval_minutes=options.sync_interval,
    )
    client = httpx.AsyncClient(timeout=10)

    async def shutdown() -> None:
        await client.aclose()
        if store is not None:
            store.close()

    app = create_app(
        GetMarketSnapshot(BinancePublicMarketData(client)),
        options.config,
        on_shutdown=shutdown,
        orders=store,
        order_sync=order_sync,
    )
    # Open SSE streams never end on their own; cap the wait on Ctrl+C.
    uvicorn.run(app, host=options.host, port=options.port, timeout_graceful_shutdown=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
