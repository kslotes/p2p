"""Signed Binance C2C (P2P) order history adapter (USER_DATA endpoint).

Credentials are handed in by the caller and only ever leave this object as
the ``X-MBX-APIKEY`` header and the HMAC signature. They never appear in
exceptions or in ``repr``.
"""

import hashlib
import hmac
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from p2p.domain.orders import (
    Order,
    OrderHistoryError,
    OrderRole,
    OrderSide,
    OrderStatus,
)

API_URL = "https://api.binance.com"
HISTORY_PATH = "/sapi/v1/c2c/orderMatch/listUserOrderHistory"
TIME_PATH = "/api/v3/time"
SUCCESS_CODE = "000000"
PAGE_SIZE = 50  # the endpoint never returns more rows per page, whatever is asked
MAX_PAGES = 100  # 5,000 orders in one 30-day window: far beyond any real account
MAX_WINDOW = timedelta(days=30)
RECV_WINDOW_MS = 5_000

CLOCK_CODE = "-1021"
AUTH_CODES = frozenset({"-2014", "-2015"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Row(_Model):
    orderNumber: str
    advNo: str
    tradeType: OrderSide
    asset: str
    fiat: str
    fiatSymbol: str | None = None
    amount: Decimal
    totalPrice: Decimal
    unitPrice: Decimal
    orderStatus: str
    createTime: int  # epoch milliseconds
    commission: Decimal | None = None
    takerCommission: Decimal | None = None
    counterPartNickName: str | None = None
    payMethodName: str | None = None
    advertisementRole: OrderRole


class _HistoryResponse(_Model):
    code: str
    message: str | None = None
    total: int | None = None
    data: list[_Row] | None = None


def _local_ms() -> int:
    return time.time_ns() // 1_000_000


def _to_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class BinanceC2COrderHistory:
    """``OrderHistorySource`` over ``/sapi/v1/c2c/orderMatch/listUserOrderHistory``.

    The offset between the local clock and Binance is read once from
    ``/api/v3/time`` and reused; a ``-1021`` (timestamp outside recvWindow)
    refreshes it and retries the request once.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = API_URL,
        recv_window: int = RECV_WINDOW_MS,
        now_ms: Callable[[], int] = _local_ms,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._secret = api_secret.encode()
        self._base_url = base_url.rstrip("/")
        self._recv_window = recv_window
        self._now_ms = now_ms
        self._offset_ms: int | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self._base_url!r})"

    async def fetch(
        self, side: OrderSide, start: datetime, end: datetime
    ) -> list[Order]:
        if end - start > MAX_WINDOW:
            raise ValueError("the order history range must span at most 30 days")
        orders: list[Order] = []
        for page in range(1, MAX_PAGES + 1):
            parsed = await self._page(side, _to_ms(start), _to_ms(end), page)
            rows = parsed.data or []
            orders.extend(_to_domain(row) for row in rows)
            if not rows:
                return orders
            if parsed.total is not None:
                if len(orders) >= parsed.total:
                    return orders
            elif len(rows) < PAGE_SIZE:  # no total reported: a short page is the last
                return orders
        raise OrderHistoryError(
            f"Binance order history did not end after {MAX_PAGES} pages; giving up"
        )

    async def _page(
        self, side: OrderSide, start_ms: int, end_ms: int, page: int
    ) -> _HistoryResponse:
        params = {
            "tradeType": side.value,
            "startTimestamp": start_ms,
            "endTimestamp": end_ms,
            "page": page,
            "rows": PAGE_SIZE,
        }
        for _attempt in range(2):
            response = await self._signed_get(HISTORY_PATH, params)
            body = _json(response)
            if _code(body) != CLOCK_CODE:
                return _parse(response, body)
            self._offset_ms = None  # resync with the server clock, then retry
        raise OrderHistoryError(
            "Binance rejected the request timestamp even after syncing with the "
            "server time (code -1021); check that this computer's clock is "
            "synchronized"
        )

    async def _signed_get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        if self._offset_ms is None:
            self._offset_ms = await self._server_offset()
        query = urlencode(
            {
                **params,
                "recvWindow": self._recv_window,
                "timestamp": self._now_ms() + self._offset_ms,
            }
        )
        signature = hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()
        url = f"{self._base_url}{path}?{query}&signature={signature}"
        return await self._get(url, headers={"X-MBX-APIKEY": self._api_key})

    async def _server_offset(self) -> int:
        response = await self._get(f"{self._base_url}{TIME_PATH}")
        body = _json(response)
        server_ms = body.get("serverTime") if isinstance(body, dict) else None
        if response.status_code != 200 or not isinstance(server_ms, int):
            raise OrderHistoryError(
                f"Could not read the Binance server time (HTTP {response.status_code})"
            )
        return server_ms - self._now_ms()

    async def _get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        try:
            return await self._client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            # Only the class name: transport messages can echo the signed URL.
            raise OrderHistoryError(
                f"Binance request failed ({type(exc).__name__}); check the connection"
            ) from None


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _code(body: Any) -> str | None:
    if isinstance(body, dict) and body.get("code") is not None:
        return str(body["code"])
    return None


def _parse(response: httpx.Response, body: Any) -> _HistoryResponse:
    code = _code(body)
    message = ""
    if isinstance(body, dict):
        message = str(body.get("msg") or body.get("message") or "")
    if code in AUTH_CODES or response.status_code == 401:
        raise OrderHistoryError(
            f"Binance rejected the API key ({code or 'HTTP 401'}): check that the "
            "key and secret are correct, that the key has read permission, and "
            "that this machine's public IP is in the key's IP access restriction "
            "list"
        )
    if response.status_code >= 400 or code != SUCCESS_CODE:
        detail = code or f"HTTP {response.status_code}"
        raise OrderHistoryError(
            f"Binance API error {detail}: {message or 'no message'}"
        )
    try:
        # Validate from raw JSON so amounts become exact Decimals.
        return _HistoryResponse.model_validate_json(response.content)
    except ValidationError as exc:
        raise OrderHistoryError(
            f"Unexpected Binance order history payload: {exc}"
        ) from None


def _to_domain(row: _Row) -> Order:
    created_at = datetime.fromtimestamp(row.createTime // 1000, UTC) + timedelta(
        milliseconds=row.createTime % 1000
    )
    return Order(
        order_number=row.orderNumber,
        adv_no=row.advNo,
        side=row.tradeType,
        asset=row.asset,
        fiat=row.fiat,
        fiat_symbol=row.fiatSymbol or "",
        amount=row.amount,
        total_price=row.totalPrice,
        unit_price=row.unitPrice,
        status=OrderStatus.parse(row.orderStatus),
        created_at=created_at,
        role=row.advertisementRole,
        commission=row.commission,
        taker_commission=row.takerCommission,
        counterparty=row.counterPartNickName or "",
        pay_method=row.payMethodName or "",
    )
