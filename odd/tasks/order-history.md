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
- [x] T3 SQLite order store: schema, idempotent upsert, Decimal as TEXT, filtered query, last-sync cursor. Tests on `:memory:`.
- [x] T4 `p2p-sync` CLI + `.env` loader + `data/` gitignore + README section (API key setup: read-only, IP restriction).
- [x] T5 Web: periodic sync in the app lifecycle when credentials exist, `/api/orders` (filters: asset, side, status, date range; totals over COMPLETED), sync status endpoint/field, History tab UI.
- [x] T6 Calculator: default maker fee from the effective maker rate of completed MAKER orders (fallback 0.20%; user-entered value still wins).
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
- T3 done. Route: delegated writer (trigger: 2+ non-trivial files, store plus tests). RED: `uv run pytest -q` -> 1 collection error (`p2p.adapters.sqlite_order_store` missing). GREEN: `uv run pytest -q` -> 138 passed; ruff check and format clean. Decisions: amounts as TEXT (`str(Decimal)` keeps scale, e.g. `0.0700`), `created_at` as integer UTC ms computed with integer timedelta math, index on `created_at`, `PRAGMA user_version = 1`; upsert overwrites every column on conflict; query filters combine with AND, `start` inclusive and `end` exclusive, newest first (ties by order number); the "last-sync cursor" is derived from the data (`latest_created_at`, `oldest_open_created_at`) rather than a separate table; the connection is opened with `check_same_thread=False` behind a lock so the web app can share it. Commit: `feat(orders): add SQLite order store`.
- T1-T3 verification: `gentle-ai review assess --base-ref 56c48cc --committed-only` -> risk high, RDD off -> independent read-only verifier: pass-with-notes, no defects (pytest 138 passed, ruff check and format clean). Parent spot check: `uv run pytest -q` -> 138 passed. Notes carried forward: keep the `httpx` logger at WARNING (it logs signed URLs at INFO) -> T4; do not surface raw validation errors (may quote nicknames) in the web UI -> T5; optional: store the raw unknown status string, reuse integer ms conversion in the adapter.

- T4 done. Route: delegated writer (trigger: 2+ non-trivial files, `config.py` loader plus new `sync_cli.py`, tests and README). RED: `uv run pytest -q` -> 2 collection errors (`p2p.sync_cli` missing, new names missing from `p2p.config`). GREEN: `uv run --no-sync pytest -q` -> 157 passed; ruff check and format clean; `uv run --no-sync python -m p2p.sync_cli --help` shows `--db` / `--env-file`. `uv run p2p-sync --help` could not run: a running `p2p-web.exe` locks the venv, so uv cannot reinstall the package to add the new script (os error 32); re-run after stopping the web app. Decisions: `Credentials` masks both key and secret in `repr`/`str`; `load_env` reads `utf-8-sig`, supports an optional `export ` prefix, no inline comments; non-empty real env vars win, an empty one does not hide the file value; `P2P_DB_PATH` may come from env or `.env`, `--db` wins; the CLI takes `make_source`/`environ`/`console` keyword args for tests; `httpx`/`httpcore` loggers raised to at least WARNING on every run; `OrderHistoryError` printed as rich `Text` (no markup injection); exit codes 0/1/2, Ctrl+C exits 1 keeping progress. `p2p.domain` and `p2p.application` now re-export the order types, ports and `SyncOrderHistory`/`SyncResult`. `.gitignore` adds `data/` and `*.sqlite`. Commit: `feat(orders): add p2p-sync CLI and .env credentials loader`.

- T4 verification: assess risk high (RDD off) -> independent read-only verifier: pass-with-notes, no defects (pytest 157 passed, ruff clean, `python -m p2p.sync_cli --help` ok). Parent spot check: `uv run --no-sync pytest -q` -> 157 passed. README notes fixed (inline `#` stays in the value, only non-empty env vars win, Ctrl+C exits 1). Deferred, optional: friendly message for `sqlite3.Error`/`OSError`, `--db ""` ignored, restore logger levels in one test. `uv run p2p-sync --help` pending: a running `p2p-web` locks the venv on Windows.

- T7 (partial, early) 2026-10-21: the user ran `uv run p2p-sync` with the real read-only key. `data/p2p.sqlite` holds 77 orders (BUY COMPLETED 71, BUY CANCELLED 3, SELL COMPLETED 3), matching the 2026-10-20 probe. History tab check still pending (T5).

- T5 done. Route: delegated writer (trigger: 2+ non-trivial files: new `adapters/web/order_sync_runner.py`, `app.py`, `main.py`, static page, tests, README). RED: `uv run pytest -q` -> 1 collection error (`build_order_history` missing from `p2p.adapters.web.main`); after the backend, 1 failed (page had no History tab). GREEN: `uv run pytest -q` -> 189 passed; ruff check and format clean; `node --check app.js` ok; `uv run p2p-web --help` lists `--sync-interval`/`--db`/`--env-file`; `uv run p2p-sync --help` now runs (venv no longer locked). Decisions: `OrderSyncRunner` runs the blocking sync (its own `asyncio.run` + `httpx.AsyncClient` per run) in a daemon thread so the monitor loop never waits and Ctrl+C never waits for a backfill; one sync at a time (`trigger` is a no-op while running); first run at startup, then every `--sync-interval` minutes (default 5, `0` disables the automatic runs, Sync now still works); errors kept as a short safe message: first line of an `OrderHistoryError` (payload validation details quote input such as nicknames), max 200 chars, class name only for anything else, logged at WARNING with the same text; `httpx`/`httpcore` loggers raised to WARNING. Without credentials an existing DB is opened (not created) and the status says not configured. `/api/orders` filters `asset` (alnum, 1-20, uppercased), `side`, `status`, `from`/`to` as UTC days with both ends included (from > to -> 422), totals = domain `summarize` over the filtered orders (COMPLETED only, per side/asset/fiat, no cross-asset grand total), `assets` lists every stored asset for the filter; cancelled orders return `fee: null` (commission never charged). `GET /api/orders/sync` status; `POST /api/orders/sync` -> 409 when not configured. UI: Monitor/History tabs (`p2p.tab` in localStorage, try/catch), filters, totals table, orders table in its own scroll container, status line + Sync now (polls status every 3 s while running, 60 s otherwise, reloads orders after a new successful sync), setup note pointing to the README; all server text via `textContent`, a 422 shows a fixed message (never the server detail). `p2p-web` `parse_args` now returns a `WebOptions` named tuple. Commit: `feat(web): add order history tab with background sync`.

- T6 done. Route: delegated writer (same trigger as T5: domain helper, API, page script/markup, tests, README). RED: `uv run pytest -q` -> 1 collection error (`fee_percent` missing from `p2p.domain.fees`); `tests/test_web_orders.py` -> 3 failed (`/api/orders/maker-fee` 404 x2, page without the hint). GREEN: `uv run pytest -q` -> 197 passed; ruff check and format clean; `node --check app.js` ok. Decisions: separate `GET /api/orders/maker-fee` -> `{"maker_fee_pct": "0.20" | null}` = domain `effective_maker_fee_rate` over every stored COMPLETED order (not the History filters), through new pure `fee_percent` (rate x 100, 2 decimals, half up, never `-0.00`); null without a DB or without completed maker orders with a commission. Calculator: when nothing is stored in the browser it fetches the real rate and applies it only if the field still holds the 0.20 default and storage is still empty, showing a "from your orders" chip; typing a fee stores it, hides the chip, and wins from then on; the real rate itself is never stored. Calculator note and README updated (the rate mixes every asset traded as a maker). Not testable in the repo: the browser-side fallback/override flow (no JS test runner); covered by markup/script assertions only. Commit: `feat(web): default calculator maker fee to the real rate from synced orders`.
- T5-T6 verification: assess `--base-ref c05f6b6` -> risk high (RDD off) -> independent read-only verifier: pass-with-notes, no defects (pytest 197 passed, ruff clean, `node --check` ok; concurrency, data exposure, API, no-credentials path, calculator precedence and boundaries checked). Parent spot check: `uv run pytest -q` -> 197 passed. Optional follow-ups: on shutdown the sync thread can outlive `store.close()` (error swallowed, transaction rolls back, no corruption); `/api/orders` runs an extra unfiltered query for the asset list (use `SELECT DISTINCT` if it grows); clearing the fee field stores `""`.

## Next step

T7: browser check of the History tab by the user (`uv run p2p-web`; tab switching and remembered tab, filters, totals, Sync now and status line, light/dark, phone width with the table scrolling inside its container) and of the calculator's "from your orders" fee default.
