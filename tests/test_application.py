import asyncio
from decimal import Decimal

from p2p.application import GetMarketSnapshot, MarketDataPort
from p2p.domain import Ad, Advertiser, Side


class FakeMarketData:
    def __init__(self, prices: dict[Side, list[str]]) -> None:
        self.prices = prices
        self.calls: list[tuple[str, str, Side, int, tuple[str, ...]]] = []

    async def fetch_ads(
        self,
        asset: str,
        fiat: str,
        side: Side,
        rows: int,
        pay_types: tuple[str, ...] = (),
    ) -> list[Ad]:
        self.calls.append((asset, fiat, side, rows, pay_types))
        return [
            Ad(
                ad_id=f"{side.value}-{i}",
                side=side,
                asset=asset,
                fiat=fiat,
                price=Decimal(price),
                available=Decimal(1),
                min_amount=Decimal(1),
                max_amount=Decimal(2),
                payment_methods=(),
                advertiser=Advertiser(nick="n", month_orders=0, finish_rate=Decimal(0)),
            )
            for i, price in enumerate(self.prices[side])
        ]


def test_fake_satisfies_the_port():
    port: MarketDataPort = FakeMarketData({Side.BUY: [], Side.SELL: []})
    assert port is not None


def test_use_case_fetches_both_sides_and_builds_a_snapshot():
    fake = FakeMarketData({Side.SELL: ["1210", "1205"], Side.BUY: ["1190"]})

    snapshot = asyncio.run(
        GetMarketSnapshot(fake).execute(
            asset="USDT", fiat="ARS", rows=5, pay_types=("Mercadopago",)
        )
    )

    assert sorted(call[2].value for call in fake.calls) == ["BUY", "SELL"]
    assert all(call[3] == 5 and call[4] == ("Mercadopago",) for call in fake.calls)
    assert snapshot.asset == "USDT" and snapshot.fiat == "ARS"
    assert snapshot.best_ask is not None and snapshot.best_ask.price == Decimal(1205)
    assert snapshot.best_bid is not None and snapshot.best_bid.price == Decimal(1190)
    assert snapshot.spread is not None
    assert snapshot.spread.absolute == Decimal(15)
