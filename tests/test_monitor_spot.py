import asyncio
from decimal import Decimal

from p2p.application import GetMarketSnapshot, SpotPricePort
from p2p.application.monitor import Config, MonitorState, refresh
from p2p.domain import Ad, Advertiser, MarketDataError, Side


def make_ad(side: Side, price: str, asset: str) -> Ad:
    return Ad(
        ad_id=f"{asset}-{side}-{price}",
        side=side,
        asset=asset,
        fiat="ARS",
        price=Decimal(price),
        available=Decimal(1),
        min_amount=Decimal(1000),
        max_amount=Decimal(250000),
        payment_methods=("Mercadopago",),
        advertiser=Advertiser(nick="n", month_orders=1, finish_rate=Decimal(1)),
    )


class FakeMarketData:
    async def fetch_ads(self, asset, fiat, side, rows, pay_types=()):
        return [make_ad(side, "150000000", asset)]


class FakeSpot:
    def __init__(self, prices: dict[str, Decimal]) -> None:
        self.prices = prices
        self.calls: list[tuple[str, ...]] = []
        self.failing = False

    async def fetch_usdt_prices(self, assets):
        self.calls.append(assets)
        if self.failing:
            raise MarketDataError("spot is down")
        return {asset: self.prices[asset] for asset in assets}


def make_config(*assets: str) -> Config:
    return Config(
        assets=assets, fiat="ARS", rows=5, interval=60, pay_types=(), once=False
    )


def run(spot: FakeSpot | None, state: MonitorState, *assets: str) -> None:
    use_case = GetMarketSnapshot(FakeMarketData())
    asyncio.run(refresh(use_case, make_config(*assets), state, spot=spot))


def test_fake_spot_satisfies_the_port():
    spot: SpotPricePort = FakeSpot({})
    assert spot is not None


def test_refresh_fetches_spot_prices_for_non_stablecoins_only():
    spot = FakeSpot({"BTC": Decimal(100000), "ETH": Decimal(4000)})
    state = MonitorState()

    run(spot, state, "USDT", "BTC", "usdc", "ETH")

    assert spot.calls == [("BTC", "ETH")]
    assert state.spot_prices == {"BTC": Decimal(100000), "ETH": Decimal(4000)}
    assert state.spot_error is None
    assert set(state.snapshots) == {"USDT", "BTC", "usdc", "ETH"}


def test_spot_failure_keeps_last_good_prices_and_records_the_error():
    spot = FakeSpot({"BTC": Decimal(100000)})
    state = MonitorState()
    run(spot, state, "BTC")

    spot.failing = True
    run(spot, state, "BTC")

    assert state.spot_prices == {"BTC": Decimal(100000)}
    assert state.spot_error is not None and "spot is down" in state.spot_error
    assert "BTC" in state.snapshots and state.errors == {}  # P2P data unaffected

    spot.failing = False
    run(spot, state, "BTC")
    assert state.spot_error is None


def test_only_stablecoins_need_no_spot_request():
    spot = FakeSpot({})
    state = MonitorState()

    run(spot, state, "USDT", "USDC")

    assert spot.calls == []
    assert state.spot_prices == {} and state.spot_error is None


def test_refresh_without_a_spot_port_leaves_spot_state_untouched():
    state = MonitorState()

    asyncio.run(refresh(GetMarketSnapshot(FakeMarketData()), make_config("BTC"), state))

    assert state.spot_prices == {} and state.spot_error is None
    assert "BTC" in state.snapshots
