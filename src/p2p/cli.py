"""Terminal entrypoint: live Binance P2P price monitor (read-only)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from decimal import Decimal

import httpx
from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from p2p.adapters.binance_public import BinancePublicMarketData
from p2p.application import GetMarketSnapshot
from p2p.application.monitor import Config, MonitorState, refresh
from p2p.config import add_monitor_arguments, config_from_args
from p2p.domain import Ad, MarketSnapshot

MAX_PAYMENT_METHODS_SHOWN = 3
VERIFIED_TICK = "✓"  # check mark before verified merchant nicks


def parse_args(argv: Sequence[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(
        prog="p2p-monitor",
        description="Live Binance P2P price monitor (public data, read-only).",
    )
    add_monitor_arguments(parser)
    parser.add_argument(
        "--once", action="store_true", help="Render a single snapshot and exit."
    )
    return config_from_args(parser, parser.parse_args(argv))


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _quantity(value: Decimal) -> str:
    text = f"{value:,.8f}".rstrip("0").rstrip(".")
    return text or "0"


def _compact(value: Decimal) -> str:
    """Short fiat amount for limits: 1000 -> 1K, 9800000 -> 9.8M."""
    for threshold, suffix in ((Decimal(10**9), "B"), (Decimal(10**6), "M")):
        if abs(value) >= threshold:
            return f"{value / threshold:.2f}".rstrip("0").rstrip(".") + suffix
    if abs(value) >= 1000:
        return f"{value / 1000:.2f}".rstrip("0").rstrip(".") + "K"
    return f"{value:.2f}".rstrip("0").rstrip(".") or "0"


def _percent(ratio: Decimal) -> str:
    return f"{ratio * 100:.1f}%"


def _methods(ad: Ad) -> str:
    shown = ad.payment_methods[:MAX_PAYMENT_METHODS_SHOWN]
    extra = len(ad.payment_methods) - len(shown)
    return ", ".join(shown) + (f" +{extra}" if extra > 0 else "")


def _advertiser(ad: Ad) -> Text:
    # The tick goes first so a long nick truncated with an ellipsis keeps it.
    text = Text()
    if ad.advertiser.verified:
        text.append(f"{VERIFIED_TICK} ", style="bold cyan")
    text.append(ad.advertiser.nick)
    return text


def _ads_table(title: str, ads: Sequence[Ad], fiat: str, style: str) -> Table:
    table = Table(
        title=title,
        title_style=style,
        title_justify="left",
        expand=True,
        box=box.SIMPLE_HEAD,
        show_edge=False,
        pad_edge=False,
    )
    # Numbers never wrap or truncate; free-text columns absorb narrow terminals.
    table.add_column(f"Price {fiat}", justify="right", style=style, no_wrap=True)
    table.add_column("Available", justify="right", no_wrap=True)
    table.add_column(f"Limits {fiat}", justify="right", no_wrap=True)
    table.add_column("Payment methods", overflow="fold", ratio=2, min_width=12)
    table.add_column("Advertiser", overflow="ellipsis", ratio=1, min_width=10)
    table.add_column("Orders 30d", justify="right", no_wrap=True)
    for ad in ads:
        table.add_row(
            _money(ad.price),
            _quantity(ad.available),
            f"{_compact(ad.min_amount)}-{_compact(ad.max_amount)}",
            _methods(ad),
            _advertiser(ad),
            f"{ad.advertiser.month_orders} @ {_percent(ad.advertiser.finish_rate)}",
        )
    if not ads:
        table.add_row("-", "", "", "no ads", "", "")
    return table


def _summary(snapshot: MarketSnapshot) -> Text:
    ask, bid, spread = snapshot.best_ask, snapshot.best_bid, snapshot.spread
    text = Text()
    text.append("Best buy price (lowest ask): ")
    text.append(_money(ask.price) if ask else "-", style="bold green")
    text.append("   Best sell price (highest bid): ")
    text.append(_money(bid.price) if bid else "-", style="bold red")
    text.append("   Spread: ")
    if spread:
        text.append(
            f"{_money(spread.absolute)} ({spread.percentage:.2f}%)", style="bold"
        )
    else:
        text.append("-")
    return text


def _asset_panel(asset: str, state: MonitorState, config: Config) -> Panel:
    parts: list[RenderableType] = []
    snapshot = state.snapshots.get(asset)
    if snapshot is not None:
        parts += [
            _summary(snapshot),
            _ads_table(
                "Sellers (you buy from them)", snapshot.asks, config.fiat, "green"
            ),
            _ads_table("Buyers (you sell to them)", snapshot.bids, config.fiat, "red"),
        ]
    if error := state.errors.get(asset):
        stale = " (showing last good data)" if snapshot is not None else ""
        parts.append(Text(f"Refresh error{stale}: {error}", style="bold yellow"))
    if not parts:
        parts.append(Text("Waiting for data...", style="dim"))
    return Panel(Group(*parts), title=f"[bold]{asset}/{config.fiat}[/bold]")


def render(state: MonitorState, config: Config) -> RenderableType:
    updated = state.updated_at.strftime("%H:%M:%S") if state.updated_at else "never"
    header = f"Binance P2P monitor | updated {updated}"
    if config.pay_types:
        header += f" | pay types: {', '.join(config.pay_types)}"
    if not config.once:
        header += f" | refresh every {config.interval:g}s | Ctrl+C to exit"
    panels = [_asset_panel(asset, state, config) for asset in config.assets]
    return Group(Text(header, style="bold cyan"), *panels)


async def _run(config: Config, console: Console) -> int:
    state = MonitorState()
    async with httpx.AsyncClient(timeout=10) as client:
        use_case = GetMarketSnapshot(BinancePublicMarketData(client))
        if config.once:
            await refresh(use_case, config, state)
            console.print(render(state, config))
            return 0 if state.snapshots else 1
        with Live(render(state, config), console=console, auto_refresh=False) as live:
            while True:
                await refresh(use_case, config, state)
                live.update(render(state, config), refresh=True)
                await asyncio.sleep(config.interval)


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv)
    # A legacy code page (cp1252 when piped on Windows) cannot encode the tick
    # or emoji in nicks; print "?" for those instead of crashing.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    console = Console()
    try:
        return asyncio.run(_run(config, console))
    except KeyboardInterrupt:
        console.print("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
