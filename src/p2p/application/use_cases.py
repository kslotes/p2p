import asyncio

from p2p.application.ports import MarketDataPort
from p2p.domain import MarketSnapshot, Side


class GetMarketSnapshot:
    """Fetch both sides of the market for one asset and build a snapshot."""

    def __init__(self, market_data: MarketDataPort) -> None:
        self._market_data = market_data

    async def execute(
        self, *, asset: str, fiat: str, rows: int, pay_types: tuple[str, ...] = ()
    ) -> MarketSnapshot:
        asks, bids = await asyncio.gather(
            self._market_data.fetch_ads(asset, fiat, Side.SELL, rows, pay_types),
            self._market_data.fetch_ads(asset, fiat, Side.BUY, rows, pay_types),
        )
        return MarketSnapshot.from_ads(asset=asset, fiat=fiat, asks=asks, bids=bids)
