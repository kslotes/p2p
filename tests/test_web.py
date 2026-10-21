import asyncio
import json
from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from p2p.adapters.web import SnapshotPoller, create_app, sse_stream
from p2p.adapters.web import poller as poller_module
from p2p.adapters.web.main import parse_args
from p2p.application import GetMarketSnapshot
from p2p.application.monitor import Config
from p2p.domain import Ad, Advertiser, MarketDataError, Side


def make_ad(side: Side, price: str, asset: str, **advertiser: str) -> Ad:
    return Ad(
        ad_id=f"{asset}-{side}-{price}",
        side=side,
        asset=asset,
        fiat="ARS",
        price=Decimal(price),
        available=Decimal("1234.5"),
        min_amount=Decimal(1000),
        max_amount=Decimal(250000),
        payment_methods=("Mercadopago", "Uala"),
        advertiser=Advertiser(
            nick=f"nick-{side.value.lower()}",
            month_orders=321,
            finish_rate=Decimal("0.985"),
            **advertiser,
        ),
    )


class FakeMarketData:
    def __init__(self, failing_assets: set[str] = frozenset()) -> None:
        self.failing_assets = failing_assets
        self.calls = 0

    async def fetch_ads(self, asset, fiat, side, rows, pay_types=()):
        self.calls += 1
        if asset in self.failing_assets:
            raise MarketDataError(f"{asset} is down")
        if side is Side.SELL:
            return [make_ad(side, "1625.50", asset), make_ad(side, "1619.00", asset)]
        return [make_ad(side, "1590.00", asset)]


def make_config(*assets: str) -> Config:
    return Config(
        assets=assets or ("USDT", "BTC"),
        fiat="ARS",
        rows=5,
        interval=3600,  # the loop never fires on its own during a test
        pay_types=(),
        once=False,
    )


def make_app(fake: FakeMarketData, config: Config | None = None):
    return create_app(GetMarketSnapshot(fake), config or make_config())


def asset_entry(payload: dict, asset: str) -> dict:
    return next(entry for entry in payload["assets"] if entry["asset"] == asset)


def test_snapshots_endpoint_serializes_both_sides_with_decimal_strings():
    app = make_app(FakeMarketData())

    with TestClient(app) as client:  # lifespan runs the first refresh
        response = client.get("/api/snapshots")

    assert response.status_code == 200
    payload = response.json()
    assert payload["fiat"] == "ARS"
    assert [entry["asset"] for entry in payload["assets"]] == ["USDT", "BTC"]
    usdt = asset_entry(payload, "USDT")
    assert usdt["best_buy"] == "1619.00"  # lowest ask
    assert usdt["best_sell"] == "1590.00"  # highest bid
    assert usdt["spread"] == {"absolute": "29.00", "percentage": "1.8239"}
    assert [ad["price"] for ad in usdt["asks"]] == ["1619.00", "1625.50"]
    assert usdt["bids"][0] == {
        "price": "1590.00",
        "available": "1234.5",
        "min_amount": "1000",
        "max_amount": "250000",
        "payment_methods": ["Mercadopago", "Uala"],
        "advertiser": {
            "nick": "nick-buy",
            "month_orders": 321,
            "finish_rate": "0.985",
            "verified": False,
            "merchant_identity": None,
        },
    }
    assert usdt["updated_at"] is not None
    assert usdt["error"] is None


class MerchantMarketData(FakeMarketData):
    async def fetch_ads(self, asset, fiat, side, rows, pay_types=()):
        merchant = {"user_type": "merchant", "merchant_identity": "BLOCK_MERCHANT"}
        return [make_ad(side, "1619.00", asset, **merchant)]


def test_snapshots_flag_verified_merchant_advertisers():
    app = make_app(MerchantMarketData(), make_config("USDT"))

    with TestClient(app) as client:
        usdt = asset_entry(client.get("/api/snapshots").json(), "USDT")

    advertiser = usdt["asks"][0]["advertiser"]
    assert advertiser["verified"] is True
    assert advertiser["merchant_identity"] == "BLOCK_MERCHANT"


def test_page_script_renders_the_verified_merchant_tick():
    script = TestClient(make_app(FakeMarketData())).get("/app.js").text

    assert "Verified merchant" in script and "advertiser.verified" in script


def test_snapshots_before_any_refresh_are_empty_placeholders():
    payload = TestClient(make_app(FakeMarketData())).get("/api/snapshots").json()

    usdt = asset_entry(payload, "USDT")
    assert usdt["best_buy"] is None and usdt["asks"] == []
    assert usdt["updated_at"] is None and usdt["error"] is None
    assert payload["updated_at"] is None


def test_failed_refresh_keeps_last_good_snapshot_and_reports_the_error():
    fake = FakeMarketData()
    app = make_app(fake)

    with TestClient(app) as client:
        poller: SnapshotPoller = app.state.poller
        fake.failing_assets = {"BTC"}
        client.portal.call(poller.refresh)
        payload = client.get("/api/snapshots").json()

    btc, usdt = asset_entry(payload, "BTC"), asset_entry(payload, "USDT")
    assert "BTC is down" in btc["error"]
    assert btc["best_buy"] == "1619.00"  # stale data stays visible
    assert usdt["error"] is None


def test_poller_refreshes_on_its_interval_and_stops_cleanly():
    async def scenario() -> int:
        fake = FakeMarketData()
        config = replace(make_config("USDT"), interval=0.01)
        poller = SnapshotPoller(GetMarketSnapshot(fake), config)
        await poller.start()
        version = poller.version
        while poller.version < version + 2:
            await poller.changed.wait()
        await poller.stop()
        return fake.calls

    # first refresh at start plus at least two loop refreshes, two sides each
    assert asyncio.run(scenario()) >= 6


def test_poller_loop_survives_an_unexpected_refresh_error(monkeypatch, caplog):
    real_refresh = poller_module.refresh
    failures = []

    async def flaky_refresh(*args):
        if not failures:
            failures.append(True)
            raise RuntimeError("boom")
        await real_refresh(*args)

    async def scenario() -> SnapshotPoller:
        config = replace(make_config("USDT"), interval=0.01)
        poller = SnapshotPoller(GetMarketSnapshot(FakeMarketData()), config)
        await poller.start()  # first refresh succeeds before the patch
        monkeypatch.setattr(poller_module, "refresh", flaky_refresh)
        version = poller.version
        async with asyncio.timeout(5):  # a dead loop would never refresh again
            while poller.version < version + 1:
                await poller.changed.wait()
        assert poller.running
        await poller.stop()
        return poller

    asyncio.run(scenario())
    assert failures == [True]
    assert "boom" in caplog.text


def test_stream_sends_the_current_payload_as_the_first_event():
    fake = FakeMarketData()
    app = make_app(fake, make_config("USDT"))
    asyncio.run(app.state.poller.refresh())

    # Without the lifespan the poller is not running, so the stream ends after
    # the initial event instead of waiting for refreshes.
    response = TestClient(app).get("/api/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(events) == 1
    assert asset_entry(events[0], "USDT")["best_buy"] == "1619.00"


def test_stream_pushes_an_event_after_every_refresh_and_ends_on_stop():
    async def scenario() -> list[str]:
        poller = SnapshotPoller(
            GetMarketSnapshot(FakeMarketData()), make_config("USDT")
        )
        await poller.start()
        stream = sse_stream(poller)
        chunks = [await anext(stream)]
        await poller.refresh()
        chunks.append(await anext(stream))
        await poller.stop()
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        return chunks

    first, second = asyncio.run(scenario())
    assert first.startswith("data: ") and first.endswith("\n\n")
    assert second.startswith("data: ")


def test_round_trip_endpoint_uses_the_domain_calculator():
    client = TestClient(make_app(FakeMarketData()))

    response = client.get(
        "/api/round-trip", params={"sell": "1618", "rebuy": "1610", "fee_pct": "0.2"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "sell": "1618",
        "rebuy": "1610",
        "fee_pct": "0.2",
        "real_sell": "1614.76",
        "real_rebuy": "1613.23",
        "difference": "1.53",
        "difference_pct": "0.0948",
        "outcome": "GAIN",
        "amount": "1000",
        "fiat_result": "1530.00",
        "asset_result": "0.95",
    }


def test_round_trip_endpoint_estimates_a_custom_amount():
    client = TestClient(make_app(FakeMarketData()))

    response = client.get(
        "/api/round-trip",
        params={"sell": "1618", "rebuy": "1610", "fee_pct": "0.35", "amount": "250"},
    )

    body = response.json()
    assert (body["amount"], body["fiat_result"], body["asset_result"]) == (
        "250",
        "-827.50",
        "-0.51",
    )


def test_round_trip_endpoint_reports_a_loss_at_a_higher_fee():
    client = TestClient(make_app(FakeMarketData()))

    response = client.get(
        "/api/round-trip", params={"sell": "1618", "rebuy": "1610", "fee_pct": "0.35"}
    )

    assert response.json()["outcome"] == "LOSS"


@pytest.mark.parametrize(
    "params",
    [
        {"sell": "0", "rebuy": "1610", "fee_pct": "0.2"},
        {"sell": "1618", "rebuy": "-1", "fee_pct": "0.2"},
        {"sell": "abc", "rebuy": "1610", "fee_pct": "0.2"},
        {"sell": "1618", "rebuy": "1610", "fee_pct": "100"},
        {"sell": "1618", "rebuy": "1610", "fee_pct": "-0.1"},
        {"sell": "NaN", "rebuy": "1610", "fee_pct": "0.2"},
        {"sell": "1618", "fee_pct": "0.2"},
        # domain rejections: price above the maximum, real leg rounding to 0.00
        {"sell": "1E+50", "rebuy": "1610", "fee_pct": "0.2"},
        {"sell": "0.001", "rebuy": "0.001", "fee_pct": "0.2"},
        {"sell": "1", "rebuy": "1000000000", "fee_pct": "99.99999999999999999"},
        # estimate amount: must be > 0 and at most the domain maximum
        {"sell": "1618", "rebuy": "1610", "fee_pct": "0.2", "amount": "0"},
        {"sell": "1618", "rebuy": "1610", "fee_pct": "0.2", "amount": "-5"},
        {"sell": "1618", "rebuy": "1610", "fee_pct": "0.2", "amount": "abc"},
        {"sell": "1618", "rebuy": "1610", "fee_pct": "0.2", "amount": "1E+12"},
    ],
)
def test_round_trip_endpoint_rejects_invalid_input(params):
    client = TestClient(make_app(FakeMarketData()))

    assert client.get("/api/round-trip", params=params).status_code == 422


def test_web_parse_args_reuses_monitor_flags_and_adds_host_and_port():
    options = parse_args(["--asset", "usdt,btc", "--interval", "5", "--port", "9000"])

    assert options.config.assets == ("USDT", "BTC")
    assert options.config.interval == 5
    assert (options.host, options.port) == ("127.0.0.1", 9000)
    defaults = parse_args([])
    assert (defaults.host, defaults.port) == ("127.0.0.1", 8000)


def test_root_serves_the_browser_monitor_page_and_its_assets():
    client = TestClient(make_app(FakeMarketData()))

    page = client.get("/")
    script = client.get("/app.js")
    styles = client.get("/styles.css")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "Maker fee %" in page.text and "app.js" in page.text
    assert "Estimate on 1,000 USDT" in page.text
    assert script.status_code == 200 and "EventSource" in script.text
    assert styles.status_code == 200
    assert client.get("/api/round-trip?sell=1&rebuy=1&fee_pct=0").status_code == 200
