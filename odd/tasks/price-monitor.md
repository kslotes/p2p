# Feature: price-monitor

## Objective

Read-only P2P price monitor for Binance: show the top ads per asset and side so the user can place competitive ads manually while rebuilding merchant status.

## Problem / Why

The user lost Binance P2P merchant status and needs ~200 real orders to reapply. Ad management through the API requires merchant permission, but public market data does not. Knowing the competition's price in real time is the base of placing ads that fill fast.

## Scope

- Assets: USDT, USDC, BTC, ETH (configurable). Fiat: ARS (configurable).
- Data source: public, unauthenticated endpoint
  `POST https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search`
  (verified 2026-10-20). Request `tradeType: "BUY"` returns SELL ads (taker perspective);
  the domain models this explicitly.
- Terminal CLI that refreshes every N seconds and renders a table per asset: best buy, best sell, spread, top-N ads (price, available amount, limits, payment methods, merchant name).

Out of scope: authenticated endpoints, ad management, persistence, Telegram alerts (later features).

## Constraints

- Hexagonal architecture: domain (pure), application (use cases), ports, adapters (httpx), CLI entrypoint.
- Python 3.13, uv, httpx, pydantic, rich. Tests with pytest; no network in tests.
- Prices handled as `Decimal`, never float.

## Tasks

| ID | Task | Route | Status |
|----|------|-------|--------|
| T1 | Project scaffold (git, uv package, deps, .gitignore) | inline (mechanical) | [x] |
| T2 | Domain models + market data port + use case (best price, spread) with tests | delegated (2+ non-trivial files) | [x] |
| T3 | Binance public adapter with fixture-based tests | delegated (2+ non-trivial files) | [ ] |
| T4 | CLI monitor loop (rich table, config flags) + README | delegated (2+ non-trivial files) | [ ] |

## Acceptance criteria

- `uv run pytest` green, `uv run ruff check` clean.
- `uv run p2p-monitor --asset USDT --fiat ARS` shows live top ads and spread, refreshing on an interval.
- Network errors are shown in the UI without crashing the loop.

## Delivery

- Strategy: ask-on-risk. Forecast ~500 authored lines; PR boundaries to be asked if exceeded.
- RDD: off (global). Delivery `disabled/unmanaged`.

## Progress / Evidence

- T1: uv 0.13.2, Python 3.13.16, deps httpx/pydantic/rich, dev pytest/ruff.
- T2: RED observed (ImportError on `p2p.domain`), then GREEN `uv run pytest -q` 7 passed; `ruff check` and `ruff format --check` clean. Domain speaks in advertiser `Side`; asks = SELL ads (cheapest first), bids = BUY ads (highest first); spread % is relative to the best bid. Commit: `feat(domain): add ad models, market data port and snapshot use case`.

## Next step

T3.
