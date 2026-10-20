# p2p

Read-only Binance P2P price monitor for the terminal. It polls the public
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

## Layout

Hexagonal, inside `src/p2p`:

- `domain/`: pure models (`Ad`, advertiser `Side`, `MarketSnapshot`, `Spread`), `Decimal` prices.
- `application/`: `MarketDataPort` protocol and the `GetMarketSnapshot` use case.
- `adapters/binance_public.py`: httpx + pydantic implementation of the port.
- `cli.py`: argument parsing, refresh loop and rich rendering.

## Development

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests never hit the network; the adapter is tested against a captured
response in `tests/fixtures/`.
