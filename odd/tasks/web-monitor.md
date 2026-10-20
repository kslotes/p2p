# Feature: web-monitor

## Objective

Browser UI for the live P2P price monitor (same data as `p2p-monitor`), plus a small round-trip fee calculator: sell price and rebuy price in, real prices after the maker fee and the real difference out, labelled loss / break-even / gain.

## Problem / Why

Watching the monitor in a terminal is uncomfortable. A web page sets up the later features (price history, alerts) that do not fit the console. The calculator answers "does this sell/rebuy pair make or lose money after fees?" at a glance while placing ads as a non-verified (non-merchant) user.

## Scope

- New driving adapter: FastAPI app exposing the existing `GetMarketSnapshot` use case. One shared background poller hits Binance (never one poll per browser client); clients get JSON plus live updates over SSE.
- Static frontend (HTML + vanilla JS + CSS served by FastAPI): one panel per asset (best buy, best sell, spread, top ads per side), same taker-perspective labels as the CLI.
- Compact calculator widget: inputs sell price, rebuy price, maker fee % (editable, default 0.20%, remembered in the browser). Output: real sell, real rebuy, real difference (absolute and %), status loss / BE / gain.
- Fee math lives in the domain (pure, `Decimal`) and is served by an API endpoint so future alerts reuse it.

Out of scope: history persistence, alerts, auth, deployment.

## Fee model (unverified)

- Binance P2P: takers pay 0; makers (advertisers) pay a % that depends on fiat/pair. Public sources give 0-0.35% (USDT historically 0.20%); no official ARS figure could be verified (2026-10-20). Hence the editable fee input; the user should confirm the fee shown in the app when publishing an ad.
- Conservative model, fee charged in crypto on each leg:
  - real sell = sell x (1 - fee)
  - real rebuy = rebuy / (1 - fee)
  - difference = real sell - real rebuy; BE when it rounds to 0.00.

## Constraints

- Hexagonal: domain and application untouched except the new pure fee function; the web adapter only depends on the application layer.
- Python 3.13, uv, `Decimal` money, pytest without network (fake port), ruff clean.
- Frontend without a build step or SPA framework.

## Tasks

| ID | Task | Route | Status |
|----|------|-------|--------|
| T5 | Domain round-trip fee calculator (`Decimal`) with tests | delegated (writer for T5-T7, 2+ non-trivial files) | [x] |
| T6 | FastAPI web adapter: shared poller + cache, `/api/snapshots`, `/api/stream` (SSE), `/api/round-trip`, `p2p-web` entrypoint, tests | delegated | [ ] |
| T7 | Static frontend (asset panels, live updates, calculator widget) + README section | delegated | [ ] |

## Acceptance criteria

- `uv run pytest -q` green, `uv run ruff check .` and `uv run ruff format --check .` clean.
- `uv run p2p-web` serves the page; panels refresh live; a failed refresh keeps the last good data and shows the error.
- Calculator example: sell 1618, rebuy 1610, fee 0.20% -> real sell 1614.76, real rebuy 1613.23, difference +1.53 (gain).

## Delivery

- Branch `feat/web-monitor` from `feat/price-monitor`.
- Forecast ~750 authored lines (> 400): strategy ask-on-risk, chain strategy `stacked-to-main` (user choice). Each slice targets `main` and merges in order; `feat/price-monitor` must merge first.
- Slices: PR1 = T5 + T6 (domain fee calc + web adapter/API); PR2 = T7 (frontend + README).
- RDD: off (global). Delivery `disabled/unmanaged`.

## Progress / Evidence

- T5 (delegated): `domain/fees.py` with `round_trip()` -> frozen `RoundTrip` + `Outcome` (LOSS / BREAK_EVEN / GAIN). Real legs are rounded to cents (ROUND_HALF_UP) before the difference, which is what makes the acceptance example land on 1.53 (unrounded legs give 1.53755); `difference_pct` relative to real rebuy, 4 decimals.
  - RED: `uv run pytest -q tests/test_fees.py` -> `ImportError: cannot import name 'Outcome'`.
  - GREEN: `uv run pytest -q` -> 32 passed; `ruff check` clean; `ruff format --check` clean.
  - Commit: `feat(domain): add round-trip maker fee calculator` (hash recorded with T6).

## Next step

T6 (FastAPI adapter).
