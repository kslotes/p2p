import asyncio
import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from p2p.adapters.binance_spot import TICKER_URL, BinancePublicSpot
from p2p.domain import MarketDataError

TICKERS = '[{"symbol":"BTCUSDT","price":"107123.45000000"},{"symbol":"ETHUSDT","price":"3890.12000000"}]'


def run_fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    assets: tuple[str, ...] = ("BTC", "ETH"),
):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await BinancePublicSpot(client).fetch_usdt_prices(assets)

    return asyncio.run(go())


def test_requests_every_usdt_symbol_at_once_and_maps_prices_back_to_assets():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=TICKERS)

    prices = run_fetch(handler)

    assert prices == {
        "BTC": Decimal("107123.45000000"),
        "ETH": Decimal("3890.12000000"),
    }
    assert str(prices["BTC"]) == "107123.45000000"  # exact, never via float
    (request,) = seen
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == TICKER_URL
    assert request.url.params["symbols"] == '["BTCUSDT","ETHUSDT"]'


def test_lower_case_assets_are_requested_upper_case_and_keep_their_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["symbols"] == '["BTCUSDT"]'
        return httpx.Response(200, content='[{"symbol":"BTCUSDT","price":"1.5"}]')

    assert run_fetch(handler, ("btc",)) == {"btc": Decimal("1.5")}


def test_no_assets_means_no_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert run_fetch(handler, ()) == {}


def test_http_errors_become_market_data_errors():
    with pytest.raises(MarketDataError, match="spot request failed"):
        run_fetch(lambda request: httpx.Response(400, json={"code": -1121}))


def test_network_errors_become_market_data_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(MarketDataError, match="offline"):
        run_fetch(handler)


def test_unexpected_payloads_become_market_data_errors():
    with pytest.raises(MarketDataError, match="Unexpected Binance spot payload"):
        run_fetch(lambda request: httpx.Response(200, json={"price": "1"}))


def test_a_missing_symbol_is_an_error():
    body = json.dumps([{"symbol": "BTCUSDT", "price": "1"}])
    with pytest.raises(MarketDataError, match="ETHUSDT"):
        run_fetch(lambda request: httpx.Response(200, content=body))
