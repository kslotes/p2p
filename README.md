# p2p

Read-only Binance P2P price monitor for the terminal and the browser. It polls the public
(unauthenticated) P2P ad search endpoint and shows, per asset, the best price
to buy, the best price to sell, the spread, and the top ads on each side.

No API keys, no account access, no ad management.

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

## Web UI

Same data in the browser, plus a round-trip fee calculator:

```sh
uv run p2p-web                              # http://127.0.0.1:8000
uv run p2p-web --asset USDT,BTC --interval 30 --port 8765
```

It takes the same `--asset`, `--fiat`, `--rows`, `--interval` and
`--pay-type` flags as `p2p-monitor`, plus:

| Flag | Default | Meaning |
|------|---------|---------|
| `--host` | `127.0.0.1` | Bind address. Keep it local unless you mean to expose it. |
| `--port` | `8000` | Bind port. |

How it behaves:

- One shared poller fetches every asset each interval; browsers read its cache, so opening more tabs does not add Binance requests. The first fetch runs before the server starts accepting requests.
- Panels update live over Server-Sent Events and fall back to polling if the stream drops. Labels match the terminal (see above).
- A failed refresh shows the error in that asset's panel and keeps the last good data.

| Endpoint | Returns |
|----------|---------|
| `GET /` | The monitor page. |
| `GET /api/snapshots` | Latest snapshot per asset (JSON, money as decimal strings). |
| `GET /api/stream` | The same payload as SSE: once on connect, then after every refresh. |
| `GET /api/round-trip?sell=&rebuy=&fee_pct=` | Calculator result; `fee_pct` is a percent (`0.2` = 0.20%). 422 on invalid input. |

### Round-trip calculator

Answers "if I sell at X and buy back at Y as a maker, do I make or lose money
after fees?". Enter sell price, rebuy price and maker fee % (default `0.20`,
remembered by the browser).

The fee is modeled as charged in crypto on each leg:

| Value | Formula |
|-------|---------|
| Real sell | `sell x (1 - fee)`, rounded to cents |
| Real rebuy | `rebuy / (1 - fee)`, rounded to cents |
| Real difference | `real sell - real rebuy`; % relative to real rebuy |
| Status | `GAIN` if > 0, `LOSS` if < 0, `BREAK-EVEN` if 0.00 |

Example: sell 1618, rebuy 1610, fee 0.20% -> real sell 1614.76, real rebuy
1613.23, difference +1.53 (GAIN). At 0.35% the same pair is a LOSS (-3.31).

> **The fee is unverified.** Binance P2P takers pay no fee; makers pay a
> percentage that depends on the pair (public sources show 0-0.35%). Check the
> fee the Binance app shows when you publish an ad and enter that value.

## Layout

Hexagonal, inside `src/p2p`:

- `domain/`: pure models (`Ad`, advertiser `Side`, `MarketSnapshot`, `Spread`), `Decimal` prices.
- `application/`: `MarketDataPort` protocol, the `GetMarketSnapshot` use case and the shared live-monitor state (`monitor.py`: `Config`, `MonitorState`, `refresh`).
- `adapters/binance_public.py`: httpx + pydantic implementation of the port.
- `domain/fees.py`: pure round-trip maker fee calculator (`round_trip`, `Outcome`).
- `adapters/web/`: FastAPI app (`app.py`), shared background poller (`poller.py`), `p2p-web` entrypoint (`main.py`) and the static page (`static/`, vanilla JS, no build step).
- `config.py`: monitor flags and their validation, shared by `p2p-monitor` and `p2p-web`.
- `cli.py`: `p2p-monitor` entrypoint, refresh loop and rich rendering.

## Development

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests never hit the network; the adapter is tested against a captured
response in `tests/fixtures/`.
