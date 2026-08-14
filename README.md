# Verge — BTC Signal Filter

A personal decision-support tool for Polymarket's BTC Up/Down market. Verge is a **bet filter with a memory** — it doesn't trade, it tells you when the math says a bet is worth considering.

Supports both **1-hour** and **15-minute** BTC markets.

## What It Does

Every hour (or 15 minutes), Polymarket runs a binary market: "Will BTC go UP or DOWN?" Verge analyzes real-time price data, computes technical indicators, and tells you whether the market is mispriced enough to bet on — or whether you should **SKIP**.

The dashboard shows:
- **BET HIGHER** / **BET LOWER** / **SKIP** — the decision
- A live chart with price movement and strike price
- The indicators behind the decision (RSI, volume, MA trend, divergence)
- Edge after fees — the real reason to bet or not
- Fear & Greed index
- Historical signal log with batch summaries
- Window timeline with observation history
- Diagnostics page with price source validation

## How It Works

### Data Pipeline

```
Polymarket WS (TWAP 60s) → Chainlink on-chain → Pyth oracle → Coinbase spot → candle close
     ↓
Price source chain (closest-to-true-source first)
     ↓
Binance / CoinGecko OHLCV → indicators
     ↓
Polymarket Gamma API → market discovery + odds
     ↓
Indicators → Score → Decision → Fee check → Final call
```

### Price Source Chain

Verge uses a layered price source chain, preferring the source closest to what Polymarket resolves against:

| Priority | Source | Method | Latency |
|---|---|---|---|
| 1 | **Polymarket WS TWAP** | 60-second time-weighted average from live WebSocket ticks | ~5 min (heartbeat) |
| 2 | **Chainlink on-chain** | Direct Polygon RPC contract read | ~5 min |
| 3 | **Pyth oracle** | Free Pyth Network API | ~5 min |
| 4 | **Coinbase spot** | Coinbase ticker API | ~5 min |
| 5 | **Candle close** | Last 5m candle close | ~5 min |

### Indicators

Three technical signals are computed from the last 50 five-minute candles:

| Indicator | What It Measures | Weight |
|---|---|---|
| **RSI (14)** | Overbought (>70) or oversold (<30) momentum | 40% |
| **MA Crossover** | Fast 5-period vs slow 15-period trend direction | 25% |
| **Volume Spike** | Unusual volume (≥3× average) confirming direction | 35% |

An optional **divergence signal** (price vs RSI disagreement) adds additional context.

### Scoring Formula

Each indicator votes `+1` (up), `-1` (down), or `0` (neutral). They're combined:

```
score = (RSI_vote × 0.40) + (MA_vote × 0.25) + (Volume_vote × 0.35)
```

| Score Range | Decision | Confidence |
|---|---|---|
| ≥ +0.6 | BET HIGHER | High |
| +0.4 to +0.6 | BET HIGHER | Low |
| -0.4 to +0.4 | **SKIP** | — |
| -0.6 to -0.4 | BET LOWER | Low |
| ≤ -0.6 | BET LOWER | High |

### Fee-Adjusted Edge

Polymarket charges a taker fee. Verge computes the **real** edge after fees:

```
fee_rate = 0.07 × odds × (1 - odds)
edge = (model_probability - market_odds) - fee_rate
```

If `edge < 3.0%`, the decision is automatically downgraded to **SKIP** regardless of indicator score.

## Architecture

```
verge/
├── backend/
│   ├── app.py                    # Flask API (all endpoints + heartbeat)
│   ├── engine.py                 # Signal generation, resolution, TWAP, tick recording
│   ├── indicators.py             # RSI, MA crossover, volume spike, divergence, scoring
│   ├── data_fetcher.py           # Binance / Coinbase / CoinGecko + Polymarket WS
│   ├── chainlink_fetcher.py      # Chainlink on-chain Data Feed (Polygon RPC)
│   ├── pyth_fetcher.py           # Pyth Network oracle price feed
│   ├── polymarket_fetcher.py     # Gamma API + resolution queries
│   ├── data_alignment.py         # Price-odds alignment for backtesting
│   ├── market_config.py          # Per-duration config (1h + 15m)
│   ├── backtest.py               # Directional + mispricing backtests
│   ├── db.py                     # Supabase schema + queries
│   ├── telegram.py               # Telegram alerts + bot listener
│   ├── migrations/               # SQL migrations (001–010)
│   ├── tests/                    # 53 unit tests
│   ├── requirements.txt          # Pinned Python dependencies
│   └── Procfile                  # Render deployment
├── frontend/
│   ├── index.html                # Single-file dashboard (vanilla JS + canvas)
│   └── verge.json                # Vercel config
├── render.yaml                   # Render deployment config
├── PRD_SignalTracker.md          # Product requirements
├── BUILD_PLAN_SignalTracker.md   # Implementation roadmap
└── VERGE.md                      # Design system
```

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/signal` | GET | Full live signal (decision, indicators, odds, prices) |
| `/api/candles` | GET | Candle data + strike + spot for mini-chart |
| `/api/spot` | GET | Lightweight spot price |
| `/api/heartbeat` | GET | Persists signals, resolves windows, sends alerts |
| `/api/stats` | GET | Aggregate stats (win rate, P&L, graduation gate) |
| `/api/performance` | GET | Rolling performance over last 200 signals |
| `/api/signal-log` | GET | Paginated signal log with resolution data |
| `/api/signal-log/<id>` | GET | Single signal detail (deep-link via #signal/N) |
| `/api/signal-log/batches` | GET | Batch summaries (groups of 200) |
| `/api/window-observations` | GET | Within-window observation timeline |
| `/api/window-outcomes/recent` | GET | Recent windows with outcomes |
| `/api/phase2-progress` | GET | 15m window resolution progress (target: 300) |
| `/api/diagnostics` | GET | Price source breakdown, live prices, TWAP vs tick, resolution agreement |
| `/api/frozen` | GET | Currently frozen durations |
| `/api/admin/freeze` | POST | Freeze/unfreeze a duration |
| `/api/weekly-digest` | GET | Send Telegram weekly digest |
| `/api/debug` | GET | Supabase connection check + row counts |
| `/api/debug/resolve-status` | GET | Resolution health per duration |
| `/api/health` | GET | Health check |

All endpoints require the `X-Secret` header or `?secret=` query param.

## Resolution

### Local Resolution
Verge resolves markets locally for speed:
- **1h**: Binance 1h candle open vs close
- **15m**: Chainlink ticks from `price_snapshots` table (fallback: Coinbase → Binance 5m)

### Polymarket Validation
After local resolution, Verge queries Polymarket's official outcome via the Gamma API:
- Fetches `conditionId` from the market, queries `GET /markets/{conditionId}`
- Compares `outcomePrices` settlement against local result
- Logs mismatches and stores agreement data in `window_outcomes`

## Diagnostics Page

The diagnostics page (`/diagnostics`) shows:

- **Live Prices** — real-time price from each source (TWAP, Chainlink, Pyth, Coinbase)
- **TWAP vs Single Tick** — side-by-side comparison with difference %
- **Source Breakdown** — which sources are active, 24h usage
- **Resolution Accuracy** — per-source prediction accuracy (BET decisions only)
- **Resolution Agreement** — Verge vs Polymarket official per duration
- **Polymarket Live Market Prices** — strike price comparison
- **Recent Signals** — last 20 signals with duration + decision filters

## Deployment

- **Backend**: Render — `https://verge-1-i4zv.onrender.com`
- **Frontend**: Vercel — `https://vergesignals.vercel.app`
- **Database**: Supabase (PostgreSQL)
- **Monitoring**: cron-job.org (heartbeat every 5 minutes)

## Telegram Bot

- Real-time signal alerts with deep-linking to signal detail
- Hourly 15m summary with batch performance
- `/start` command with welcome message
- Bot polling listener (runs in background thread)

## Running Locally

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Set environment variables
export SUPABASE_URL=https://your-project.supabase.co
export SUPABASE_ANON_KEY=your-key
export VERGE_SECRET=your-secret
export TELEGRAM_BOT_TOKEN=your-token
export TELEGRAM_CHAT_ID=your-chat-id

# Run
python app.py  # Starts on localhost:5001
```

Then open `frontend/index.html` in a browser.

## Database Schema

Key tables:
- **signals** — every signal generated (decision, prices, indicators, source, condition_id)
- **paper_trades** — simulated trades with resolution and P&L
- **price_snapshots** — accumulated price ticks (Chainlink, Polymarket WS) for TWAP
- **window_observations** — within-window snapshots (15m)
- **window_outcomes** — true UP/DOWN result + Polymarket official outcome
- **odds_snapshots** — historical odds for each market
- **settings** — mode toggle and phase progress

## Key Constants

| Constant | Value | Purpose |
|---|---|---|
| RSI period | 14 | Wilder's RSI lookback |
| RSI overbought/oversold | 70 / 30 | Extreme momentum thresholds |
| MA fast/slow | 5 / 15 | Crossover periods (5m candles) |
| Volume spike ratio | 3× | Must be 3x average to count |
| Score weights | RSI 40%, Volume 35%, MA 25% | Composite scoring |
| Min viable edge | 3.0% | Below this → SKIP |
| Taker fee model | 7% × odds × (1-odds) | Polymarket fee simulation |
| Suggested price discount | 5% | Limit order improvement |
| Graduation gate | 200 trades + positive P&L | Real order unlock |
| TWAP window | 60 seconds | Time-weighted average period |
| 15m window | 900,000 ms | 15-minute market duration |

## Disclaimer

Verge is a **paper trading** decision-support tool. It does not place real bets. All trading decisions are your own. The indicators are based on technical analysis and do not guarantee outcomes. Crypto markets are volatile and unpredictable.
