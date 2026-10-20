"""Web entrypoint: serve the live monitor over HTTP (read-only, local by default)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import httpx
import uvicorn

from p2p.adapters.binance_public import BinancePublicMarketData
from p2p.adapters.web.app import create_app
from p2p.application import GetMarketSnapshot
from p2p.application.monitor import Config
from p2p.config import add_monitor_arguments, config_from_args


def parse_args(argv: Sequence[str] | None = None) -> tuple[Config, str, int]:
    parser = argparse.ArgumentParser(
        prog="p2p-web",
        description="Live Binance P2P price monitor in the browser "
        "(public data, read-only).",
    )
    add_monitor_arguments(parser)
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Bind port (default: 8000)."
    )
    args = parser.parse_args(argv)
    return config_from_args(parser, args), args.host, args.port


def main(argv: Sequence[str] | None = None) -> int:
    config, host, port = parse_args(argv)
    client = httpx.AsyncClient(timeout=10)
    app = create_app(
        GetMarketSnapshot(BinancePublicMarketData(client)),
        config,
        on_shutdown=client.aclose,
    )
    # Open SSE streams never end on their own; cap the wait on Ctrl+C.
    uvicorn.run(app, host=host, port=port, timeout_graceful_shutdown=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
