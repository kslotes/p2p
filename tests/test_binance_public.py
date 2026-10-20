import asyncio
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from p2p.adapters.binance_public import SEARCH_URL, BinancePublicMarketData
from p2p.domain import MarketDataError, Side

FIXTURE = Path(__file__).parent / "fixtures" / "binance_search_buy_usdt_ars.json"


def fixture_json() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def run_fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    side: Side = Side.SELL,
    pay_types: tuple[str, ...] = (),
):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = BinancePublicMarketData(client)
            return await adapter.fetch_ads("USDT", "ARS", side, 3, pay_types)

    return asyncio.run(go())


def test_maps_fixture_ads_to_domain():
    ads = run_fetch(lambda request: httpx.Response(200, json=fixture_json()))

    assert len(ads) == 3
    first = ads[0]
    assert first.ad_id == "12923944689060954112"
    assert first.side is Side.SELL
    assert (first.asset, first.fiat) == ("USDT", "ARS")
    assert first.price == Decimal("1619.00")
    assert first.available == Decimal("10675.41")
    assert first.min_amount == Decimal(1000000)
    assert first.max_amount == Decimal(9800000)
    assert "Mercadopago" in first.payment_methods
    assert first.advertiser.nick == "MAURO-Z"
    assert first.advertiser.month_orders == 196
    assert first.advertiser.finish_rate == Decimal("1.0")
    assert first.advertiser.user_type == "user"


@pytest.mark.parametrize(
    ("side", "expected_trade_type"), [(Side.SELL, "BUY"), (Side.BUY, "SELL")]
)
def test_requests_the_taker_intent_opposite_to_the_advertiser_side(
    side, expected_trade_type
):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert str(request.url) == SEARCH_URL
        payload = fixture_json()
        for item in payload["data"]:
            item["adv"]["tradeType"] = side.value
        return httpx.Response(200, json=payload)

    run_fetch(handler, side=side, pay_types=("MercadoPagoNew",))

    body = seen[0]
    assert body["tradeType"] == expected_trade_type
    assert (body["asset"], body["fiat"], body["rows"]) == ("USDT", "ARS", 3)
    assert body["payTypes"] == ["MercadoPagoNew"]


def test_api_error_code_raises_market_data_error():
    payload = {"code": "100001", "message": "bad request", "data": None}

    with pytest.raises(MarketDataError, match="100001"):
        run_fetch(lambda request: httpx.Response(200, json=payload))


def test_http_error_raises_market_data_error():
    with pytest.raises(MarketDataError):
        run_fetch(lambda request: httpx.Response(503, text="unavailable"))


def test_network_error_raises_market_data_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(MarketDataError):
        run_fetch(handler)


def test_unexpected_ad_side_raises_market_data_error():
    with pytest.raises(MarketDataError, match="side"):
        run_fetch(
            lambda request: httpx.Response(200, json=fixture_json()), side=Side.BUY
        )


def test_malformed_payload_raises_market_data_error():
    with pytest.raises(MarketDataError):
        run_fetch(lambda request: httpx.Response(200, json={"code": "000000"}))
