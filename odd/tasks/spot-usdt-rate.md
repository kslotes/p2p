# Feature: spot-usdt-rate

## Objective

For non-stablecoin assets (BTC, ETH) show next to every ad price, in the web UI, the implied fiat-per-USDT rate: `ad price (fiat/asset) / spot price (USDT/asset)`. Example: `150.000.000 (≈ 1.500 ARS/USDT)`.

## Problem / Why

BTC/ETH ad prices are not comparable with USDT ads at a glance. The implied rate answers "at what price am I effectively paying/receiving USDT if I trade through BTC/ETH?".

## Scope

- Spot prices from Binance public spot API (`GET https://api.binance.com/api/v3/ticker/price`, `<ASSET>USDT` symbols), no auth.
- New application port `SpotPricePort`, adapter `BinancePublicSpot`, fetched once per poll tick alongside snapshots; failures recorded, last good spot kept.
- Pure domain function for the implied rate (`Decimal`); stablecoins (USDT, USDC, FDUSD, DAI, TUSD) get no conversion.
- `/api/snapshots` (and SSE): per asset `spot_price` (USDT, or null), per ad and for best buy/sell an implied `usdt_rate` (null for stablecoins or without spot).
- Frontend: rate in parentheses after the price in ad tables and best buy/sell lines.

Out of scope: terminal CLI (`p2p-monitor`) display, calculator changes.

## Constraints

- Hexagonal layering as in `web-monitor`; CLI behaviour unchanged (spot port optional in `refresh`).
- Python 3.13, uv, `Decimal`, pytest without network, ruff clean.

## Tasks

| ID | Task | Route | Status |
|----|------|-------|--------|
| T1 | Domain implied-rate function + stablecoin set; `SpotPricePort`; `BinancePublicSpot` adapter; spot in `MonitorState`/`refresh`; API payload fields; wiring in `p2p-web`; frontend display; tests; README note | delegated (writer, 2+ non-trivial files) | [x] |

## Acceptance criteria

- `uv run pytest -q` green, `uv run ruff check .` and `uv run ruff format --check .` clean.
- BTC ad 150000000 with spot 100000 -> `usdt_rate` "1500.00"; USDT/USDC ads -> null.
- Spot failure keeps P2P data, rates fall back to last good spot or null.

## Delivery

- Branch `feat/spot-usdt-rate` from `main`. Forecast ~300 authored lines (< 400): single work unit.
- RDD: off (global). Delivery `disabled/unmanaged`.

## Progress / Evidence

- 2026-10-21: document created; user confirmed the implied ARS/USDT interpretation.
- 2026-10-21 T1: RED observed first (`uv run pytest -q` -> 3 collection errors: new `tests/test_spot.py`, `tests/test_binance_spot.py`, `tests/test_monitor_spot.py` could not import the missing code; new `test_web.py` payload tests failed).
- 2026-10-21 T1 GREEN: `uv run pytest -q` -> 228 passed; `uv run ruff check .` -> All checks passed!; `uv run ruff format --check .` -> 46 files already formatted.
- 2026-10-21 T1 live smoke: `BinancePublicSpot(client).fetch_usdt_prices(("BTC", "ETH"))` -> `{'BTC': Decimal('84526.00000000'), 'ETH': Decimal('2511.66000000')}`.
- T1 notes: spot failures reuse `MarketDataError` (no new error type); a symbol missing from the spot response is an error; spot error is shown in the header settings line; prices render with the existing en-US `fmtMoney` (e.g. `150,000,000.00 (≈ 1,500.00 ARS/USDT)`).

## Next step

Parent review of T1, then the work-unit commit.
