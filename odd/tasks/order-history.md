# Feature: order-history

## Objective

Automatically sync the user's Binance P2P order history into a local SQLite database and show it in the web monitor (History tab with filters and totals). Use the real maker commission from the orders to back the round-trip calculator's fee default.

## Problem / Why

The user tracks P2P orders by hand. Binance only exposes the last 6 months of history, so older orders are lost unless they are stored locally. Most of the user's current orders are 120-180 days old (probe 2026-10-20), so the first backfill is time sensitive.

## Data source (verified 2026-10-20 with a live probe)

- `GET https://api.binance.com/sapi/v1/c2c/orderMatch/listUserOrderHistory`, USER_DATA: `X-MBX-APIKEY` header + HMAC-SHA256 `signature` over the query string (`timestamp`, `recvWindow`).
- Params: `tradeType` (BUY/SELL, required per call), `startTimestamp`, `endTimestamp` (max 30 days apart), `page`, `rows`. Only the last 6 months are served.
- Works for a non-merchant ("Usuario regular") account with a read-only key.
- `rows=100` returns at most 50 rows (`total=55`, 50 rows) -> paginate by `total`, page size 50.
- The user's PC clock was ~1.7 s ahead of Binance -> `-1021 Timestamp ... ahead of the server's time`. Sync the offset from `GET /api/v3/time` before signing and send `recvWindow`.
- Success envelope: `{"code": "000000", "total": N, "data": [...]}`.
- Order fields used: `orderNumber`, `advNo`, `tradeType`, `asset`, `fiat`, `fiatSymbol`, `amount`, `totalPrice`, `unitPrice`, `orderStatus`, `createTime` (ms), `commission` (maker fee, null for takers), `takerCommission`, `takerCommissionRate`, `takerAmount`, `counterPartNickName`, `payMethodName`, `advertisementRole` (MAKER/TAKER).
- Maker fee observed: 0.33 / 166.11 and 4.02 / 2010.94 = 0.20%. CANCELLED orders also carry a commission value that was not charged.

## Scope

- Domain: `Order` model (Decimal money, side, status, role, commissions) and pure aggregation (totals over COMPLETED orders, effective maker fee rate).
- Application: ports `OrderHistorySource` and `OrderStore`; `SyncOrderHistory` use case (6-month backfill on first run, incremental afterwards, re-checks non-final orders so status changes land).
- Adapters: signed Binance C2C history client; SQLite store via stdlib `sqlite3` (amounts stored as TEXT to keep `Decimal` exact; upsert keyed by `orderNumber`).
- Driving: `p2p-sync` CLI; periodic sync inside the web app when credentials exist; `/api/orders` with filters and totals; History tab in the frontend; calculator fee default from the real maker rate when available.

Out of scope: trading or ad management, alerts, price history, multi-user, deployment.

## Constraints

- Hexagonal, as in the rest of the repo: domain pure, application depends on ports only, adapters never import each other.
- No new runtime dependencies: stdlib `sqlite3`, existing `httpx`; a tiny `.env` loader instead of `python-dotenv`.
- Credentials only from `BINANCE_API_KEY` / `BINANCE_API_SECRET` (env or `.env`, gitignored). Never log, print or store them. The web app and CLI must work (monitor only) without credentials.
- Database at `data/p2p.sqlite` by default, gitignored.
- Tests never touch the network or the real `.env`; fixtures are synthetic (no real counterparty names or order numbers).
- Work directly on `main` (user preference, 2026-10-20): one Conventional Commit per task, no branches, no PRs. Push stays the user's decision.

## Tasks

- [x] T1 Domain + application: `Order` model, enums, aggregation; ports `OrderHistorySource`, `OrderStore`; `SyncOrderHistory` use case with 30-day windows, 6-month backfill, incremental cursor and non-final re-check. Tests with fakes.
- [x] T2 Binance C2C history adapter: signing, server-time offset, pagination, both trade types, error mapping (clear message for unauthorized IP / bad key / timestamp). Tests with `httpx.MockTransport` + synthetic fixture.
- [ ] T3 SQLite order store: schema, idempotent upsert, Decimal as TEXT, filtered query, last-sync cursor. Tests on `:memory:`.
- [ ] T4 `p2p-sync` CLI + `.env` loader + `data/` gitignore + README section (API key setup: read-only, IP restriction).
- [ ] T5 Web: periodic sync in the app lifecycle when credentials exist, `/api/orders` (filters: asset, side, status, date range; totals over COMPLETED), sync status endpoint/field, History tab UI.
- [ ] T6 Calculator: default maker fee from the effective maker rate of completed MAKER orders (fallback 0.20%; user-entered value still wins).
- [ ] T7 Live smoke: real sync against Binance with the user's key, DB row count matches the probe (~77 orders in 6 months), History tab renders.

## Acceptance criteria

- `uv run p2p-sync` backfills 6 months on an empty DB and a second run adds no duplicates.
- Orders whose status changes on Binance are updated locally on the next sync.
- History tab lists orders with filters and totals; totals ignore non-COMPLETED orders.
- Missing credentials: the monitor still works and the History tab explains how to configure the key.
- `uv run pytest -q`, `uv run ruff check`, `uv run ruff format --check` clean.

## Delivery

- Forecast: ~1,200-1,600 authored changed lines across T1-T6 (over the 400-line heuristic). Strategy: `exception-ok` — work-unit commits directly on `main` per user preference; no PRs, so no chain slicing.
- RDD: off (decided by global). Verification per task follows the RDD-off tier table (`gentle-ai review assess`).

## Progress

- T1 done. Route: delegated writer (trigger: 2+ non-trivial files across domain and application). RED: `uv run pytest -q` -> 2 collection errors (`p2p.domain.orders` / `p2p.application.order_sync` missing). GREEN: `uv run pytest -q` -> 111 passed; ruff check and format clean. Decisions: new `OrderSide` enum (user's own side, distinct from the advertiser `Side`); unknown Binance statuses map to `OrderStatus.UNKNOWN`, which is non-final (re-checked every sync) and never counted in totals; `Order.fee` is the commission of the user's role (null -> 0); `effective_maker_fee_rate` skips makers with a null commission; sync overlap is 1 day, windows are fetched oldest first and upserted per window (a failure keeps earlier progress), orders seen twice in one run are upserted once. New modules are imported by full path (`p2p.domain.orders`, `p2p.application.order_sync`); package `__init__` re-exports were outside the edit surface. Commit: `feat(orders): add order model, aggregation and history sync use case`.
- T2 done. Route: delegated writer (trigger: 2+ non-trivial files, adapter plus tests and fixture). RED: `uv run pytest -q` -> 1 collection error (`p2p.adapters.binance_c2c_history` missing). GREEN: `uv run pytest -q` -> 130 passed; ruff check and format clean. Decisions: `OrderHistoryError` lives in the domain so the application can catch it; server-time offset cached per adapter instance, reset and retried once on -1021; paging stops at `total`, on an empty page, or (without a total) on a short page, and raises after 100 pages; auth errors (-2014, -2015, HTTP 401) name key, read permission and the IP access restriction; transport errors report only the exception class (messages can echo the signed URL); `repr` shows only the base URL; ranges over 30 days raise `ValueError`. Synthetic fixture `tests/fixtures/c2c_order_history_sell.json`. Commit: `feat(orders): add signed Binance C2C order history adapter`.

## Next step

T1-T3 via one delegated writer (trigger: 2+ non-trivial files across domain, application, adapters).
