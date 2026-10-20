import asyncio
from decimal import Decimal

import pytest
from rich.console import Console

from p2p.application import GetMarketSnapshot
from p2p.cli import MonitorState, parse_args, refresh, render
from p2p.domain import Ad, Advertiser, MarketDataError, Side


def make_ad(side: Side, price: str, asset: str = "USDT") -> Ad:
    return Ad(
        ad_id=f"{side}-{price}",
        side=side,
        asset=asset,
        fiat="ARS",
        price=Decimal(price),
        available=Decimal("1234.5"),
        min_amount=Decimal(1000),
        max_amount=Decimal(250000),
        payment_methods=("Mercadopago", "Uala"),
        advertiser=Advertiser(
            nick=f"nick-{side.value.lower()}",
            month_orders=321,
            finish_rate=Decimal("0.985"),
        ),
    )


class FakeMarketData:
    def __init__(self, failing_assets: set[str] = frozenset()) -> None:
        self.failing_assets = failing_assets

    async def fetch_ads(self, asset, fiat, side, rows, pay_types=()):
        if asset in self.failing_assets:
            raise MarketDataError(f"{asset} is down")
        price = "1619.00" if side is Side.SELL else "1590.00"
        return [make_ad(side, price, asset)]


def test_parse_args_defaults():
    config = parse_args([])

    assert config.assets == ("USDT", "USDC", "BTC", "ETH")
    assert config.fiat == "ARS"
    assert config.rows == 5
    assert config.interval == 15
    assert config.pay_types == ()
    assert config.once is False


def test_parse_args_accepts_repeated_and_comma_separated_values():
    config = parse_args(
        [
            "--asset",
            "usdt,btc",
            "--asset",
            "ETH",
            "--asset",
            "btc",
            "--fiat",
            "ars",
            "--rows",
            "3",
            "--interval",
            "2.5",
            "--pay-type",
            "MercadoPagoNew",
            "--once",
        ]
    )

    assert config.assets == ("USDT", "BTC", "ETH")
    assert config.fiat == "ARS"
    assert config.rows == 3
    assert config.interval == 2.5
    assert config.pay_types == ("MercadoPagoNew",)
    assert config.once is True


@pytest.mark.parametrize(
    "argv", [["--rows", "0"], ["--rows", "21"], ["--interval", "0"]]
)
def test_parse_args_rejects_invalid_values(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_refresh_records_errors_per_asset_and_keeps_last_good_snapshot():
    config = parse_args(["--asset", "USDT,BTC"])
    fake = FakeMarketData()
    use_case = GetMarketSnapshot(fake)
    state = MonitorState()

    asyncio.run(refresh(use_case, config, state))
    assert set(state.snapshots) == {"USDT", "BTC"}
    assert state.errors == {}

    fake.failing_assets = {"BTC"}
    asyncio.run(refresh(use_case, config, state))

    assert "BTC is down" in state.errors["BTC"]
    assert "BTC" in state.snapshots  # stale data stays visible
    assert "USDT" not in state.errors


def test_render_shows_best_prices_spread_ads_and_errors():
    config = parse_args(["--asset", "USDT,BTC", "--once"])
    use_case = GetMarketSnapshot(FakeMarketData(failing_assets={"BTC"}))
    state = MonitorState()
    asyncio.run(refresh(use_case, config, state))

    console = Console(record=True, width=160)
    console.print(render(state, config))
    text = console.export_text()

    assert "USDT/ARS" in text
    assert "1,619.00" in text and "1,590.00" in text
    assert "1.82%" in text  # (1619 - 1590) / 1590
    assert "nick-sell" in text and "nick-buy" in text
    assert "Mercadopago" in text
    assert "98.5%" in text
    assert "1K-250K" in text  # compact fiat limits
    assert "BTC is down" in text
