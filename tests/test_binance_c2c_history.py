import asyncio
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import pytest

from p2p.adapters.binance_c2c_history import (
    API_URL,
    HISTORY_PATH,
    PAGE_SIZE,
    TIME_PATH,
    BinanceC2COrderHistory,
)
from p2p.domain.orders import OrderHistoryError, OrderRole, OrderSide, OrderStatus

FIXTURE = Path(__file__).parent / "fixtures" / "c2c_order_history_sell.json"
API_KEY = "fake-api-key-123"
API_SECRET = "fake-api-secret-456"
LOCAL_MS = 1_790_000_000_000
SERVER_MS = LOCAL_MS + 1_700  # the local clock lags 1.7 s here
START = datetime(2026, 9, 20, tzinfo=UTC)
END = START + timedelta(days=30)


def fixture_json() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def time_response() -> httpx.Response:
    return httpx.Response(200, json={"serverTime": SERVER_MS})


def history_params(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.url.query.decode()))


class Handler:
    """Serves /api/v3/time and delegates history calls to ``history``."""

    def __init__(self, history: Callable[[httpx.Request], httpx.Response]) -> None:
        self.history = history
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == TIME_PATH:
            return time_response()
        assert request.url.path == HISTORY_PATH
        return self.history(request)

    @property
    def history_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == HISTORY_PATH]

    @property
    def time_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == TIME_PATH]


def run_fetch(handler: Callable[[httpx.Request], httpx.Response], calls: int = 1):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = BinanceC2COrderHistory(
                client, api_key=API_KEY, api_secret=API_SECRET, now_ms=lambda: LOCAL_MS
            )
            results = [
                await adapter.fetch(OrderSide.SELL, START, END) for _ in range(calls)
            ]
            return results[-1]

    return asyncio.run(go())


def test_maps_fixture_rows_to_orders():
    orders = run_fetch(Handler(lambda r: httpx.Response(200, json=fixture_json())))

    assert [o.order_number for o in orders] == [
        "90000000000000000001",
        "90000000000000000002",
        "90000000000000000003",
    ]
    maker, taker, cancelled = orders
    assert maker.side is OrderSide.SELL and maker.role is OrderRole.MAKER
    assert (maker.asset, maker.fiat, maker.fiat_symbol) == ("USDT", "ARS", "$")
    assert maker.amount == Decimal("166.11")
    assert maker.total_price == Decimal("260998.5")
    assert maker.unit_price == Decimal("1571.25")
    assert maker.commission == Decimal("0.33")
    assert maker.status is OrderStatus.COMPLETED
    assert maker.created_at == datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    assert maker.counterparty == "Buyer-Alpha" and maker.pay_method == "BANK"
    assert maker.adv_no == "80000000000000000001"
    assert taker.role is OrderRole.TAKER
    assert taker.commission is None
    assert taker.taker_commission == Decimal("0.07")
    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.taker_commission is None


def test_unknown_status_maps_to_unknown_instead_of_failing():
    payload = fixture_json()
    payload["data"][0]["orderStatus"] = "BRAND_NEW_STATUS"

    orders = run_fetch(Handler(lambda r: httpx.Response(200, json=payload)))

    assert orders[0].status is OrderStatus.UNKNOWN


def test_signs_the_exact_query_with_server_time_and_api_key_header():
    handler = Handler(lambda r: httpx.Response(200, json=fixture_json()))

    run_fetch(handler)

    (request,) = handler.history_requests
    assert str(request.url).startswith(API_URL + HISTORY_PATH + "?")
    assert request.headers["X-MBX-APIKEY"] == API_KEY
    query = request.url.query.decode()
    unsigned, _, signature = query.rpartition("&signature=")
    expected = hmac.new(
        API_SECRET.encode(), unsigned.encode(), hashlib.sha256
    ).hexdigest()
    assert signature == expected
    params = history_params(request)
    assert params["tradeType"] == "SELL"
    assert params["startTimestamp"] == str(int(START.timestamp() * 1000))
    assert params["endTimestamp"] == str(int(END.timestamp() * 1000))
    assert (params["page"], params["rows"]) == ("1", str(PAGE_SIZE))
    assert PAGE_SIZE == 50
    assert params["timestamp"] == str(SERVER_MS)
    assert "recvWindow" in params


def test_server_time_offset_is_cached_between_calls():
    handler = Handler(lambda r: httpx.Response(200, json=fixture_json()))

    run_fetch(handler, calls=2)

    assert len(handler.time_requests) == 1
    assert len(handler.history_requests) == 2


def test_paginates_until_total_is_reached():
    def page(request: httpx.Request) -> httpx.Response:
        number = int(history_params(request)["page"])
        payload = fixture_json()
        row = payload["data"][0]
        size = PAGE_SIZE if number < 3 else 5
        payload["data"] = [{**row, "orderNumber": f"{number}-{i}"} for i in range(size)]
        payload["total"] = 2 * PAGE_SIZE + 5
        return httpx.Response(200, json=payload)

    handler = Handler(page)
    orders = run_fetch(handler)

    assert len(orders) == 105
    assert [history_params(r)["page"] for r in handler.history_requests] == [
        "1",
        "2",
        "3",
    ]


def test_stops_paging_on_an_empty_page_even_if_total_says_more():
    def page(request: httpx.Request) -> httpx.Response:
        payload = fixture_json()
        if history_params(request)["page"] != "1":
            payload["data"] = []
        payload["total"] = 999
        return httpx.Response(200, json=payload)

    handler = Handler(page)
    orders = run_fetch(handler)

    assert len(orders) == 3
    assert len(handler.history_requests) == 2


def test_without_a_total_a_short_page_is_the_last():
    def page(request: httpx.Request) -> httpx.Response:
        payload = fixture_json()
        del payload["total"]
        if history_params(request)["page"] == "1":
            row = payload["data"][0]
            payload["data"] = [{**row, "orderNumber": f"n-{i}"} for i in range(50)]
        return httpx.Response(200, json=payload)

    handler = Handler(page)
    orders = run_fetch(handler)

    assert len(orders) == 53
    assert len(handler.history_requests) == 2


def test_refuses_to_page_forever():
    def page(request: httpx.Request) -> httpx.Response:
        payload = fixture_json()
        payload["total"] = 10**9
        return httpx.Response(200, json=payload)

    handler = Handler(page)
    with pytest.raises(OrderHistoryError, match="pages"):
        run_fetch(handler)
    assert len(handler.history_requests) < 1000


def test_timestamp_error_resyncs_the_clock_and_retries_once():
    responses = iter(
        [
            httpx.Response(400, json={"code": -1021, "msg": "Timestamp ahead"}),
            httpx.Response(200, json=fixture_json()),
        ]
    )
    handler = Handler(lambda r: next(responses))

    orders = run_fetch(handler)

    assert len(orders) == 3
    assert len(handler.time_requests) == 2
    assert len(handler.history_requests) == 2


def test_persistent_timestamp_error_mentions_the_clock():
    handler = Handler(
        lambda r: httpx.Response(400, json={"code": -1021, "msg": "Timestamp ahead"})
    )

    with pytest.raises(OrderHistoryError, match="clock"):
        run_fetch(handler)
    assert len(handler.history_requests) == 2


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, {"code": -2015, "msg": "Invalid API-key, IP, or permissions."}),
        (400, {"code": -2014, "msg": "API-key format invalid."}),
        (401, {}),
    ],
)
def test_auth_errors_explain_key_permissions_and_ip_whitelist(status, body):
    handler = Handler(lambda r: httpx.Response(status, json=body))

    with pytest.raises(OrderHistoryError, match="IP") as caught:
        run_fetch(handler)

    message = str(caught.value)
    assert "API key" in message
    assert API_KEY not in message and API_SECRET not in message


def test_other_api_codes_raise_with_code_and_message():
    payload = {"code": "100001", "message": "bad things", "data": None}

    with pytest.raises(OrderHistoryError, match="100001.*bad things"):
        run_fetch(Handler(lambda r: httpx.Response(200, json=payload)))


def test_http_errors_without_a_body_raise():
    with pytest.raises(OrderHistoryError, match="503"):
        run_fetch(Handler(lambda r: httpx.Response(503, text="unavailable")))


def test_network_errors_raise_without_leaking_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}", request=request)

    with pytest.raises(OrderHistoryError) as caught:
        run_fetch(handler)

    message = str(caught.value)
    assert API_KEY not in message and API_SECRET not in message
    assert "signature" not in message


def test_malformed_payload_raises():
    payload = fixture_json()
    del payload["data"][0]["orderNumber"]

    with pytest.raises(OrderHistoryError, match="Unexpected"):
        run_fetch(Handler(lambda r: httpx.Response(200, json=payload)))


def test_repr_never_shows_credentials():
    adapter = BinanceC2COrderHistory(
        httpx.AsyncClient(), api_key=API_KEY, api_secret=API_SECRET
    )

    text = repr(adapter)

    assert "BinanceC2COrderHistory" in text
    assert API_KEY not in text and API_SECRET not in text


def test_rejects_windows_longer_than_30_days():
    async def go():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(Handler(lambda r: httpx.Response(200)))
        ) as client:
            adapter = BinanceC2COrderHistory(client, api_key="k", api_secret="s")
            await adapter.fetch(OrderSide.BUY, START, START + timedelta(days=31))

    with pytest.raises(ValueError, match="30 days"):
        asyncio.run(go())
