from decimal import Decimal

import pytest

from p2p.domain import Ad, Advertiser, MarketSnapshot, Side, Spread


def make_ad(side: Side, price: str, ad_id: str = "1") -> Ad:
    return Ad(
        ad_id=ad_id,
        side=side,
        asset="USDT",
        fiat="ARS",
        price=Decimal(price),
        available=Decimal(100),
        min_amount=Decimal(1000),
        max_amount=Decimal(50000),
        payment_methods=("Mercadopago",),
        advertiser=Advertiser(
            nick="alice", month_orders=10, finish_rate=Decimal("0.98")
        ),
    )


def test_taker_intent_is_the_opposite_of_the_advertiser_side():
    assert Side.SELL.counterpart is Side.BUY
    assert Side.BUY.counterpart is Side.SELL


def test_snapshot_sorts_asks_ascending_and_bids_descending():
    snapshot = MarketSnapshot.from_ads(
        asset="USDT",
        fiat="ARS",
        asks=[make_ad(Side.SELL, "1210", "a"), make_ad(Side.SELL, "1200", "b")],
        bids=[make_ad(Side.BUY, "1180", "c"), make_ad(Side.BUY, "1190", "d")],
    )

    assert [ad.ad_id for ad in snapshot.asks] == ["b", "a"]
    assert [ad.ad_id for ad in snapshot.bids] == ["d", "c"]
    assert snapshot.best_ask is not None and snapshot.best_ask.price == Decimal(1200)
    assert snapshot.best_bid is not None and snapshot.best_bid.price == Decimal(1190)


def test_snapshot_rejects_ads_on_the_wrong_side():
    with pytest.raises(ValueError):
        MarketSnapshot.from_ads(
            asset="USDT", fiat="ARS", asks=[make_ad(Side.BUY, "1")], bids=[]
        )


def test_spread_is_best_ask_minus_best_bid_with_percentage_over_bid():
    snapshot = MarketSnapshot.from_ads(
        asset="USDT",
        fiat="ARS",
        asks=[make_ad(Side.SELL, "1210")],
        bids=[make_ad(Side.BUY, "1200")],
    )

    assert snapshot.spread == Spread(
        absolute=Decimal(10), percentage=Decimal(10) / Decimal(1200) * 100
    )


def test_spread_is_none_when_a_side_is_empty():
    snapshot = MarketSnapshot.from_ads(
        asset="USDT", fiat="ARS", asks=[make_ad(Side.SELL, "1210")], bids=[]
    )

    assert snapshot.best_bid is None
    assert snapshot.spread is None
