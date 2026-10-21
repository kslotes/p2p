"""Binance spot public (unauthenticated) USDT price adapter."""

import json
from decimal import Decimal

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from p2p.domain import MarketDataError

TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
QUOTE = "USDT"


class _Ticker(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    price: Decimal


_TICKERS = TypeAdapter(list[_Ticker])


class BinancePublicSpot:
    """``SpotPricePort`` implementation over the public spot ticker endpoint."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_usdt_prices(self, assets: tuple[str, ...]) -> dict[str, Decimal]:
        if not assets:
            return {}
        symbols = {f"{asset.upper()}{QUOTE}": asset for asset in assets}
        # Binance wants a compact JSON array: ["BTCUSDT","ETHUSDT"].
        params = {"symbols": json.dumps(list(symbols), separators=(",", ":"))}
        try:
            response = await self._client.get(TICKER_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Binance spot request failed: {exc}") from exc

        try:
            # Validate from raw JSON so prices become exact Decimals.
            tickers = _TICKERS.validate_json(response.content)
        except ValidationError as exc:
            raise MarketDataError(f"Unexpected Binance spot payload: {exc}") from exc

        prices = {ticker.symbol: ticker.price for ticker in tickers}
        missing = [symbol for symbol in symbols if symbol not in prices]
        if missing:
            raise MarketDataError(f"Binance spot has no price for {', '.join(missing)}")
        return {asset: prices[symbol] for symbol, asset in symbols.items()}
