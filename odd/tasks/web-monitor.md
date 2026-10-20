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

- Hexagonal: domain and application untouched except the new pure fee function and the shared monitor state moved out of the CLI (`application/monitor.py`); the web adapter depends on the application and domain layers only (its `main.py` entrypoint also wires the Binance adapter and the shared flags in `p2p/config.py`); `cli.py` and `adapters/web` never import each other.
- Python 3.13, uv, `Decimal` money, pytest without network (fake port), ruff clean.
- Frontend without a build step or SPA framework.

## Tasks

| ID | Task | Route | Status |
|----|------|-------|--------|
| T5 | Domain round-trip fee calculator (`Decimal`) with tests | delegated (writer for T5-T7, 2+ non-trivial files) | [x] |
| T6 | FastAPI web adapter: shared poller + cache, `/api/snapshots`, `/api/stream` (SSE), `/api/round-trip`, `p2p-web` entrypoint, tests | delegated | [x] |
| T7 | Static frontend (asset panels, live updates, calculator widget) + README section | delegated | [x] |
| T8 | Verification fixes: round-trip 500s (InvalidOperation / rebuy rounding to 0, price upper bound), polling re-arm race, stale calculator result; move `Config`/`MonitorState`/`refresh` out of `p2p.cli` into a shared module | delegated (2+ non-trivial files) | [x] |
| T9 | Verified-advertiser badge: map `userType == "merchant"` (+ `userIdentity` MASS_MERCHANT / BLOCK_MERCHANT) into the domain; tick in web panels and CLI | delegated (writer for T9-T10) | [x] |
| T10 | Calculator estimate on 1,000 USDT under Real difference: fiat result = 1000 x difference; asset result = 1000 x difference / real rebuy (ARS proceeds reinvested); domain + API + UI | delegated | [ ] |

## Acceptance criteria

- `uv run pytest -q` green, `uv run ruff check .` and `uv run ruff format --check .` clean.
- `uv run p2p-web` serves the page; panels refresh live; a failed refresh keeps the last good data and shows the error.
- Calculator example: sell 1618, rebuy 1610, fee 0.20% -> real sell 1614.76, real rebuy 1613.23, difference +1.53 (gain).

## Delivery

- Branch `feat/web-monitor` from `feat/price-monitor`.
- Forecast ~750 authored lines (> 400): strategy ask-on-risk, chain strategy `stacked-to-main` (user choice). Each slice targets `main` and merges in order; `feat/price-monitor` must merge first.
- Slices: PR1 = T5 + T6 (domain fee calc + web adapter/API); PR2 = T7 (frontend + README).
- 2026-10-20 update (user): no PRs; after T8, fast-forward local `main` to `feat/web-monitor` (includes `feat/price-monitor`). No remote configured; nothing pushed.
- RDD: off (global). Delivery `disabled/unmanaged`.

## Progress / Evidence

- T5 (delegated): `domain/fees.py` with `round_trip()` -> frozen `RoundTrip` + `Outcome` (LOSS / BREAK_EVEN / GAIN). Real legs are rounded to cents (ROUND_HALF_UP) before the difference, which is what makes the acceptance example land on 1.53 (unrounded legs give 1.53755); `difference_pct` relative to real rebuy, 4 decimals.
  - RED: `uv run pytest -q tests/test_fees.py` -> `ImportError: cannot import name 'Outcome'`.
  - GREEN: `uv run pytest -q` -> 32 passed; `ruff check` clean; `ruff format --check` clean.
  - Commit: `feat(domain): add round-trip maker fee calculator` -> `7e2acf9`.
- T6 (delegated): `adapters/web/` with `create_app(use_case, config)`, `SnapshotPoller` (one shared asyncio task started in the lifespan; first refresh before serving, then every interval; reuses the CLI `refresh`/`MonitorState`, so a failed asset keeps its last good snapshot and records the error), `/api/snapshots`, `/api/stream` (SSE: initial event + one per refresh, keepalive comments, ends when the poller stops), `/api/round-trip` (Query validation + domain `ValueError` -> 422), decimals as strings. `p2p-web` = `p2p.adapters.web.main:main`; CLI flags shared via new `add_monitor_arguments`/`config_from_args`; `MonitorState.snapshot_times` adds per-asset update times. The use case is already async, so no `to_thread` was needed.
  - RED: `uv run pytest -q tests/test_web.py` -> collection error (`p2p.adapters.web` missing).
  - GREEN: `uv run pytest -q` -> 48 passed; `ruff check` clean; `ruff format --check` clean.
  - Deps: `fastapi`, `uvicorn`. Pytest filters Starlette's "use httpx2" TestClient deprecation warning.
  - Commit: `feat(web): add FastAPI adapter with shared poller, SSE and round-trip API` -> `eea07ca`.
- T7 (delegated): `adapters/web/static/` (`index.html`, `app.js`, `styles.css`) mounted at `/` after the API routes. Vanilla JS: EventSource with polling fallback (retries SSE every 30s), one panel per asset with CLI labels, per-asset error + data time without wiping last good data, all server text via `textContent`. Calculator: sell / rebuy / maker fee % (default 0.20, localStorage in try/catch), 300ms debounce, aborts stale requests, LOSS / BREAK-EVEN / GAIN badge, unverified-fee note. Light/dark via `prefers-color-scheme`; single column at <= 860px with the calculator first. README: "Web UI" section (flags, endpoints, fee formulas, unverified fee) and Layout update.
  - RED: `uv run pytest -q tests/test_web.py` -> `test_root_serves_the_browser_monitor_page_and_its_assets` failed (404).
  - GREEN: `uv run pytest -q` -> 49 passed; `ruff check` clean; `ruff format --check` clean; `node --check app.js` ok; wheel contains `p2p/adapters/web/static/*`.
  - Live smoke (`uv run p2p-web --asset USDT --interval 15 --port 8765`): `/` 200 text/html (2257 B); `/api/snapshots` USDT/ARS 5 asks + 5 bids, no error; `/api/round-trip?sell=1618&rebuy=1610&fee_pct=0.2` -> 1614.76 / 1613.23 / 1.53 / GAIN; `/api/stream` sent the initial `data:` event.
  - Commit: `feat(web): add browser monitor page with fee calculator` (this commit; see `git log`).
  - Not verified: visual check in a real browser (layout, dark mode, SSE fallback path).

- T8 (delegated writer; trigger: 2+ non-trivial files across domain, web adapter, JS and a module move). Two work units:
  - Refactor: `Config`, `MonitorState`, `refresh` -> `application/monitor.py`; `DEFAULT_ASSETS`, `MAX_ROWS`, `add_monitor_arguments`, `config_from_args` -> `p2p/config.py`. `cli.py` and `adapters/web` import those instead of each other; tests and README Layout updated. Behavior unchanged: `uv run pytest -q` -> 49 passed before and after; ruff clean. Commit `refactor(application): move shared monitor state and flags out of the CLI` -> `150f71c`.
  - Fixes: `round_trip` now rejects prices above `MAX_PRICE` (1,000,000,000), real legs that round to 0.00, and cent-quantize overflow from a fee near 100% (found while fixing: `fee_pct=99.99999999999999999` also 500'd), all as `ValueError` -> 422. `SnapshotPoller._loop` logs an unexpected `refresh` error and keeps polling (`running` stays truthful). `app.js`: a poll generation counter stops in-flight ticks from re-arming after `stopPolling()`; `calculate()` aborts the in-flight request before the empty-input early return; `normalize` handles `1,234.56`, `1.234,56`, `1234,56` and `1,234,567`; a domain 422 shows the server message.
  - RED: `uv run pytest -q tests/test_fees.py` -> `ImportError: cannot import name 'MAX_PRICE'`; `uv run pytest -q tests/test_web.py` -> 3 failed (round-trip `1E+50` and `0.001/0.001` -> 500 instead of 422; poller loop died after one raising refresh, wait timed out). Overflow case: 2 failed (domain test + API 422).
  - GREEN: `uv run pytest -q` -> 60 passed; `ruff check` clean; `ruff format --check` clean; `node --check app.js` ok; `normalize` exercised with node on 7 sample inputs.
  - Commit: `fix(web): reject out-of-range round-trip inputs and harden live updates` (this commit; see `git log`).
  - Not verified: JS polling/abort fixes in a real browser (no JS test runner in the repo).

- T9 (delegated writer; trigger: 2+ non-trivial files across domain, adapter, web, CLI). `Advertiser` gains `merchant_identity` (None for regular users, `""` normalized to None) and a pure `verified` property (`user_type == "merchant"`; null/missing = not verified). Binance adapter maps `userIdentity`. `/api/snapshots` advertiser adds `verified` + `merchant_identity`. Web: round "✓" badge after the nick (`role="img"`, `aria-label="Verified merchant"`, tooltip `Verified merchant (MASS|BLOCK)`), own light/dark tokens. CLI: bold cyan "✓ " before the nick (prefix so the Advertiser column's ellipsis cannot cut it). README "Reading the screen" legend line.
  - Found while verifying: rich on a piped Windows stdout (cp1252) raised `UnicodeEncodeError` on "✓" (also latent for emoji nicks). `cli.main` now reconfigures stdout with `errors="replace"` (prints "?" instead of crashing).
  - RED: `uv run pytest -q` -> 12 failed, 59 passed (domain `verified`, adapter merchant/block/user/missing, web payload + script, CLI tick).
  - GREEN: `uv run pytest -q` -> 71 passed; `ruff check` clean; `ruff format --check` clean; `node --check app.js` ok.
  - Commit: `feat: flag verified merchant advertisers in web and CLI` (this commit; see `git log`).
  - Not verified: tick appearance in a real browser.

## Next step

- T7-T8 merged: local `main` fast-forwarded to `e1bd5d3` (2026-10-20).
- T9-T10 on branch `feat/merchant-badge-estimate` (from `main`). Live probe 2026-10-20: verified advertisers come as `userType: "merchant"`, `userIdentity` `MASS_MERCHANT` or `BLOCK_MERCHANT`, `badges` `["Ordinary"]`/`["Block"]`; regular users `userType: "user"`, `userIdentity: ""`.
- T9 done; next T10 (estimate on 1,000 USDT).
