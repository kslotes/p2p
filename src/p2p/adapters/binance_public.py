"""Binance P2P public (unauthenticated) market data adapter."""

from decimal import Decimal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from p2p.domain import Ad, Advertiser, MarketDataError, Side

SEARCH_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
SUCCESS_CODE = "000000"


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _TradeMethod(_Model):
    tradeMethodName: str | None = None
    identifier: str | None = None


class _Adv(_Model):
    advNo: str
    tradeType: Side
    asset: str
    fiatUnit: str
    price: Decimal
    tradableQuantity: Decimal
    minSingleTransAmount: Decimal
    maxSingleTransAmount: Decimal
    tradeMethods: list[_TradeMethod] = []


class _Advertiser(_Model):
    nickName: str
    monthOrderCount: int = 0
    monthFinishRate: Decimal = Decimal(0)
    userType: str | None = None


class _Item(_Model):
    adv: _Adv
    advertiser: _Advertiser


class _SearchResponse(_Model):
    code: str
    message: str | None = None
    data: list[_Item] | None = None


class BinancePublicMarketData:
    """``MarketDataPort`` implementation over the public P2P search endpoint."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_ads(
        self,
        asset: str,
        fiat: str,
        side: Side,
        rows: int,
        pay_types: tuple[str, ...] = (),
    ) -> list[Ad]:
        # The endpoint takes the taker intent: to list advertisers who SELL we
        # must ask for tradeType=BUY, and vice versa.
        body = {
            "asset": asset,
            "fiat": fiat,
            "tradeType": side.counterpart.value,
            "page": 1,
            "rows": rows,
            "payTypes": list(pay_types),
            "publisherType": None,
        }
        try:
            response = await self._client.post(SEARCH_URL, json=body)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Binance request failed: {exc}") from exc

        try:
            # Validate from raw JSON so numbers become exact Decimals.
            parsed = _SearchResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise MarketDataError(f"Unexpected Binance payload: {exc}") from exc

        if parsed.code != SUCCESS_CODE:
            raise MarketDataError(
                f"Binance API error {parsed.code}: {parsed.message or 'no message'}"
            )
        if parsed.data is None:
            raise MarketDataError("Binance payload has no data")

        ads = [_to_domain(item) for item in parsed.data]
        if any(ad.side is not side for ad in ads):
            raise MarketDataError(
                f"Binance returned ads on an unexpected side (wanted {side.value})"
            )
        return ads


def _to_domain(item: _Item) -> Ad:
    adv, advertiser = item.adv, item.advertiser
    return Ad(
        ad_id=adv.advNo,
        side=adv.tradeType,
        asset=adv.asset,
        fiat=adv.fiatUnit,
        price=adv.price,
        available=adv.tradableQuantity,
        min_amount=adv.minSingleTransAmount,
        max_amount=adv.maxSingleTransAmount,
        payment_methods=tuple(
            method.tradeMethodName or method.identifier or "?"
            for method in adv.tradeMethods
        ),
        advertiser=Advertiser(
            nick=advertiser.nickName,
            month_orders=advertiser.monthOrderCount,
            finish_rate=advertiser.monthFinishRate,
            user_type=advertiser.userType,
        ),
    )
