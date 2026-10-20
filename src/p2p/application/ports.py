from typing import Protocol

from p2p.domain import Ad, Side


class MarketDataPort(Protocol):
    async def fetch_ads(
        self,
        asset: str,
        fiat: str,
        side: Side,
        rows: int,
        pay_types: tuple[str, ...] = (),
    ) -> list[Ad]:
        """Return up to ``rows`` ads published by advertisers on ``side``.

        Raises ``MarketDataError`` when data cannot be obtained.
        """
        ...
