"""Terminal entrypoint: live Binance P2P price monitor (read-only)."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
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
from p2p.domain import Ad, MarketSnapshot

DEFAULT_ASSETS = ("USDT", "USDC", "BTC", "ETH")
MAX_ROWS = 20  # Binance search endpoint caps page size at 20
MAX_PAYMENT_METHODS_SHOWN = 3


@dataclass(frozen=True, slots=True)
class Config:
    assets: tuple[str, ...]
    fiat: str
    rows: int
    interval: float
    pay_types: tuple[str, ...]
    once: bool


@dataclass
class MonitorState:
    """Latest good snapshot (and when it was fetched) and latest error per asset."""

    snapshots: dict[str, MarketSnapshot] = field(default_factory=dict)
    snapshot_times: dict[str, datetime] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    updated_at: datetime | None = None


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


async def refresh(
    use_case: GetMarketSnapshot, config: Config, state: MonitorState
) -> None:
    """Fetch every asset concurrently; failures are recorded, never raised."""

    async def one(asset: str) -> None:
        try:
            state.snapshots[asset] = await use_case.execute(
                asset=asset,
                fiat=config.fiat,
                rows=config.rows,
                pay_types=config.pay_types,
            )
            state.snapshot_times[asset] = datetime.now()
            state.errors.pop(asset, None)
        except Exception as exc:  # noqa: BLE001 - the monitor must survive any refresh error
            state.errors[asset] = f"{type(exc).__name__}: {exc}"

    await asyncio.gather(*(one(asset) for asset in config.assets))
    state.updated_at = datetime.now()


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
            ad.advertiser.nick,
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
    console = Console()
    try:
        return asyncio.run(_run(config, console))
    except KeyboardInterrupt:
        console.print("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
