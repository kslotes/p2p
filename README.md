# p2p

Read-only Binance P2P price monitor for the terminal and the browser. It polls the public
(unauthenticated) P2P ad search endpoint and shows, per asset, the best price
to buy, the best price to sell, the spread, and the top ads on each side.

The monitor needs no API keys, account access or ad management. The optional
[order history sync](#order-history-sync) reads your own orders with a
read-only key.

## Usage

```sh
uv sync
uv run p2p-monitor                          # USDT, USDC, BTC, ETH vs ARS, every 15s
uv run p2p-monitor --asset USDT --fiat ARS  # single asset
uv run p2p-monitor --asset usdt,btc --rows 10 --interval 30
uv run p2p-monitor --pay-type MercadoPagoNew --once
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--asset` | `USDT,USDC,BTC,ETH` | Assets to watch; repeatable or comma separated. |
| `--fiat` | `ARS` | Fiat currency. |
| `--rows` | `5` | Ads shown per side (1-20). |
| `--interval` | `15` | Seconds between refreshes. |
| `--pay-type` | none | Payment filter using Binance identifiers (e.g. `MercadoPagoNew`, `BankArgentina`), not display names. Repeatable. |
| `--once` | off | Render one snapshot and exit. |

Press `Ctrl+C` to exit. A failed refresh is shown inside the asset panel and
the last good data stays visible; the loop keeps running.

## Reading the screen

Binance phrases its search from the taker's side ("I want to buy" lists
advertisers who sell). The monitor always labels from your point of view as a
taker:

- **Sellers (you buy from them)**: asks, cheapest first. Best buy price = lowest ask.
- **Buyers (you sell to them)**: bids, highest first. Best sell price = highest bid.
- **Spread**: lowest ask minus highest bid; the percentage is relative to the highest bid.
- **Limits**: min-max fiat amount per order (`K` = thousand, `M` = million).
- **Orders 30d**: advertiser's 30-day order count and completion rate.
- **✓ before the advertiser** (after it in the web UI): Binance verified merchant (`userType` `merchant`; the web tooltip shows MASS or BLOCK).

## Web UI

Same data in the browser, plus a round-trip fee calculator and a
[History tab](#history-tab) with your synced orders:

```sh
uv run p2p-web                              # http://127.0.0.1:8000
uv run p2p-web --asset USDT,BTC --interval 30 --port 8765
```

It takes the same `--asset`, `--fiat`, `--rows`, `--interval` and
`--pay-type` flags as `p2p-monitor` (except that `--rows` defaults to `10`
here), plus:

| Flag | Default | Meaning |
|------|---------|---------|
| `--host` | `127.0.0.1` | Bind address. Keep it local unless you mean to expose it. |
| `--port` | `8000` | Bind port. |
| `--sync-interval` | `5` | Minutes between background order history syncs (needs the API key, see [order history sync](#order-history-sync)). `0` disables the automatic sync; **Sync now** still works. |
| `--db` | `$P2P_DB_PATH` or `data/p2p.sqlite` | Order database, as for `p2p-sync`. |
| `--env-file` | `./.env` | File holding the Binance credentials, as for `p2p-sync`. |

How it behaves:

- One shared poller fetches every asset each interval; browsers read its cache, so opening more tabs does not add Binance requests. The first fetch runs before the server starts accepting requests.
- Panels update live over Server-Sent Events and fall back to polling if the stream drops. Labels match the terminal (see above).
- Each panel shows sellers on the left and buyers on the right (stacked below 1280px). The "Pay" column shows the first payment method plus `+N`; hover it for the full list.
- A failed refresh shows the error in that asset's panel and keeps the last good data.
- Non-stablecoin assets (e.g. BTC, ETH) show the implied rate after every ad price and the best buy/sell prices: `150,000,000.00 (≈ 1,500.00 ARS/USDT)`, the ad price divided by the Binance spot price in USDT (`GET https://api.binance.com/api/v3/ticker/price`, fetched once per refresh with the ads). Stablecoins (USDT, USDC, FDUSD, DAI, TUSD) show none. If the spot request fails the last good spot prices stay in use and the error appears next to the refresh settings.

| Endpoint | Returns |
|----------|---------|
| `GET /` | The monitor page. |
| `GET /api/snapshots` | Latest snapshot per asset (JSON, money as decimal strings). Per asset `spot_price` (USDT), `best_buy_usdt_rate` / `best_sell_usdt_rate` and per ad `usdt_rate`, all `null` for stablecoins or without a spot price; top-level `spot_error`. |
| `GET /api/stream` | The same payload as SSE: once on connect, then after every refresh. |
| `GET /api/round-trip?sell=&rebuy=&fee_pct=[&amount=]` | Calculator result; `fee_pct` is a percent (`0.2` = 0.20%); `amount` (default 1000, max 1,000,000,000) sizes the estimate. 422 on invalid input. |
| `GET /api/orders[?asset=&side=&status=&from=&to=]` | Stored orders, newest first (money as decimal strings, times in ISO UTC), totals over the COMPLETED ones, every stored asset and the sync status. `from`/`to` are UTC days (`YYYY-MM-DD`), both included. 422 on invalid filters. |
| `GET /api/orders/maker-fee` | `{"maker_fee_pct": "0.20"}`: effective maker fee of COMPLETED MAKER orders as a percent string (2 decimals), `null` without data. |
| `GET /api/orders/sync` | Sync status: configured, running, interval, last attempt, last success, last error. |
| `POST /api/orders/sync` | Start a sync now (no-op while one is running) and return the status; 409 when no API key is configured. |

### History tab

The **History** tab lists the orders stored by the [order history
sync](#order-history-sync) (date in your local time, side, asset, amount,
unit price, total fiat, fee, role, status, counterparty, payment method),
with filters by asset, side, status and UTC date range. The totals block
only counts COMPLETED orders, per side, asset and fiat. Cancelled orders show
no fee: Binance reports one, but it was never charged. The page remembers
which tab was open.

- With the API key in `.env` (or the environment), `p2p-web` syncs once at
  startup and then every `--sync-interval` minutes, in the background: a
  slow or failing sync never blocks the price monitor. Two syncs never
  overlap. The status line shows the last successful sync and, when the last
  attempt failed, a short reason (e.g. the API key / IP restriction message);
  **Sync now** starts one immediately.
- Without the key the tab says how to configure it, and still lists the
  orders of an existing database (e.g. filled by `p2p-sync`).

### Round-trip calculator

Answers "if I sell at X and buy back at Y as a maker, do I make or lose money
after fees?". Enter sell price, rebuy price and maker fee % (a fee you type
is remembered by the browser and always wins).

Until you type a fee, it defaults to your real maker fee when the
[order history](#order-history-sync) has COMPLETED MAKER orders: total maker
commission / total amount of those orders, as a percent with 2 decimals,
marked "from your orders" (`GET /api/orders/maker-fee`). Without such orders
the default is `0.20`.

The fee is modeled as charged in crypto on each leg:

| Value | Formula |
|-------|---------|
| Real sell | `sell x (1 - fee)`, rounded to cents |
| Real rebuy | `rebuy / (1 - fee)`, rounded to cents |
| Real difference | `real sell - real rebuy`; % relative to real rebuy |
| Status | `GAIN` if > 0, `LOSS` if < 0, `BREAK-EVEN` if 0.00 |
| Estimate (fiat) | `1000 x real difference`, rounded to cents: fiat gained or lost selling and rebuying 1,000 USDT |
| Estimate (USDT) | `fiat estimate / real rebuy`, 2 decimals: USDT gained or lost when all proceeds are rebought |

Example: sell 1618, rebuy 1610, fee 0.20% -> real sell 1614.76, real rebuy
1613.23, difference +1.53 (GAIN), estimate +1,530.00 ARS (about +0.95 USDT).
At 0.35% the same pair is a LOSS (-3.31).

> **The 0.20% fallback is unverified.** Binance P2P takers pay no fee; makers
> pay a percentage that depends on the pair (public sources show 0-0.35%), and
> the rate from your orders mixes every asset you traded as a maker. Check the
> fee the Binance app shows when you publish an ad and enter that value.

## Order history sync

`p2p-sync` copies your own Binance P2P orders into a local SQLite database.
Unlike the monitor, it needs an API key, and that key only ever reads.

### 1. Create a read-only API key

1. In Binance: **Account > API Management > Create API** (system generated).
2. Leave **only "Enable Reading"** checked. Never enable trading, withdrawals
   or transfers: the sync does not need them.
3. Choose **Restrict access to trusted IPs only** and add your public IP.
   Home connections often get a new IP from time to time; when `p2p-sync`
   reports that Binance rejected the API key (it mentions the IP access
   restriction), look up your current public IP and update it in Binance.

### 2. Put the key in `.env`

Create `.env` in the directory you run the command from (it is gitignored):

```sh
BINANCE_API_KEY=your-api-key
BINANCE_API_SECRET=your-api-secret
# Optional: database location (default data/p2p.sqlite)
# P2P_DB_PATH=data/p2p.sqlite
```

One `KEY=VALUE` per line; blank lines and lines starting with `#` are
ignored, and surrounding quotes are optional. A `#` after a value is part of
the value, so do not put comments on the same line as the key or secret.
Non-empty environment variables with the same names win over the file. The
key and secret are never printed or logged.

### 3. Run it

```sh
uv run p2p-sync
uv run p2p-sync --db other.sqlite --env-file C:/secrets/binance.env
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--db` | `$P2P_DB_PATH` or `data/p2p.sqlite` | SQLite database file (created on first run). |
| `--env-file` | `./.env` | File holding the credentials. |

It prints the windows fetched, rows fetched, orders written, total orders
stored and the newest order date. Exit codes: `0` synced, `1` Binance or
network error (the message says what to fix) or interrupted with Ctrl+C
(windows already synced are kept), `2` no credentials configured.

How it behaves:

- **Binance only serves the last 6 months.** The first run backfills those
  6 months; later runs only re-read from the newest stored order (and the
  oldest still-open one, so status changes land). Orders already in the
  local database are kept forever, so run it at least every few months.
- Re-running is safe: orders are keyed by order number and updated in place.
- The database lives at `data/p2p.sqlite` by default (gitignored); amounts
  are stored as exact decimal text.

## Layout

Hexagonal, inside `src/p2p`:

- `domain/`: pure models (`Ad`, advertiser `Side`, `MarketSnapshot`, `Spread`), `Decimal` prices.
- `application/`: `MarketDataPort` protocol, the `GetMarketSnapshot` use case and the shared live-monitor state (`monitor.py`: `Config`, `MonitorState`, `refresh`).
- `adapters/binance_public.py`: httpx + pydantic implementation of the port.
- `domain/fees.py`: pure round-trip maker fee calculator (`round_trip`, `Outcome`).
- `domain/spot.py`: implied fiat-per-USDT rate and the stablecoin set; `adapters/binance_spot.py`: Binance spot prices behind the `SpotPricePort` (web UI only).
- `adapters/web/`: FastAPI app (`app.py`), shared background poller (`poller.py`), background order sync (`order_sync_runner.py`), `p2p-web` entrypoint (`main.py`) and the static page (`static/`, vanilla JS, no build step).
- `domain/orders.py`: your own P2P `Order`s and pure aggregation (totals, effective maker fee rate).
- `application/order_sync.py`: `SyncOrderHistory` use case over the `OrderHistorySource` and `OrderStore` ports.
- `adapters/binance_c2c_history.py`: signed Binance C2C order history client; `adapters/sqlite_order_store.py`: SQLite store.
- `config.py`: monitor flags and their validation, shared by `p2p-monitor` and `p2p-web`; `.env` credentials loader and database path.
- `cli.py`: `p2p-monitor` entrypoint, refresh loop and rich rendering.
- `sync_cli.py`: `p2p-sync` entrypoint.

## Development

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests never hit the network; the adapter is tested against a captured
response in `tests/fixtures/`.
