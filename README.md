# Verge — BTC Hourly Signal Filter

A personal decision-support tool for Polymarket's hourly BTC Up/Down market. Verge is a **bet filter with a memory** — it doesn't trade, it tells you when the math says a bet is worth considering.

## What It Does

Every hour, Polymarket runs a binary market: "Will BTC go UP or DOWN in the next hour?" Verge analyzes real-time price data, computes technical indicators, and tells you whether the market is mispriced enough to bet on — or whether you should **SKIP**.

The dashboard shows:
- **BET HIGHER** / **BET LOWER** / **SKIP** — the decision
- A live chart with price movement and strike price
- The indicators behind the decision (RSI, volume, MA trend)
- Edge after fees — the real reason to bet or not
- Historical signal log

## How It Works

### Data Pipeline

```
Binance (1st) → Coinbase (2nd) → CoinGecko (3rd)
     ↓
5m candles for indicators + spot price for real-time
     ↓
Polymarket Gamma API → current odds
     ↓
Indicators → Score → Decision → Fee check → Final call
```

### Indicators

Three technical signals are computed from the last 50 five-minute candles:

| Indicator | What It Measures | Weight |
|---|---|---|
| **RSI (14)** | Overbought (>70) or oversold (<30) momentum | 40% |
| **MA Crossover** | Fast 5-period vs slow 15-period trend direction | 25% |
| **Volume Spike** | Unusual volume (≥3× average) confirming direction | 35% |

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

### Model Probability

The composite score maps to an implied probability:

```
model_probability = 0.5 + score × 0.15
```

- Score = 0 → 50% (even)
- Score = +1 → 65% (strong up signal)
- Score = -1 → 35% (strong down signal)

### Fee-Adjusted Edge

Polymarket charges a taker fee. Verge computes the **real** edge after fees:

```
fee_rate = 0.07 × odds × (1 - odds)
```

At 50/50 odds, this peaks at ~1.75%. The edge is:

```
edge = (model_probability - market_odds) - fee_rate
```

If `edge < 3.0%`, the decision is automatically downgraded to **SKIP** regardless of indicator score. This is the "fee erosion" check — no bet is worth taking if the house takes too much.

### Suggested Price

If Verge recommends a bet, it suggests a limit order price at a **5% discount** below current odds to improve your edge:

```
BET HIGHER → suggested_price = odds × 0.95
BET LOWER  → suggested_price = (1 - odds) × 0.95
```

## Architecture

```
verge/
├── backend/
│   ├── app.py                 # Flask API (signal, candles, heartbeat, stats)
│   ├── engine.py              # Live signal orchestrator
│   ├── indicators.py          # RSI, MA crossover, volume spike, scoring
│   ├── data_fetcher.py        # Binance / Coinbase / CoinGecko price fetcher
│   ├── polymarket_fetcher.py  # Polymarket odds + market discovery
│   ├── data_alignment.py      # Price-odds alignment for backtesting
│   ├── backtest.py            # Directional + mispricing backtests
│   ├── report.py              # Backtest report generator
│   ├── db.py                  # Supabase schema + queries
│   ├── telegram.py            # Telegram alert delivery
│   ├── tests/                 # 48 unit tests
│   ├── requirements.txt       # Pinned Python dependencies
│   └── Procfile               # Render deployment
├── frontend/
│   ├── index.html             # Single-file dashboard (vanilla JS + canvas)
│   └── vercel.json            # Vercel config
├── render.yaml                # Render deployment
├── BUILD_PLAN_SignalTracker.md
├── PRD_SignalTracker.md
└── VERGE.md                   # Design system
```

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/signal` | GET | Full live signal (decision, indicators, odds, prices) |
| `/api/candles` | GET | 5m candles + spot price for the mini-chart |
| `/api/heartbeat` | GET | Persists signal, resolves previous hour, sends alerts |
| `/api/stats` | GET | Aggregate stats (win rate, P&L, graduation gate) |
| `/api/health` | GET | Health check |

All endpoints require the `X-Secret` header (shared access key).

## Deployment

- **Backend**: Render (Frankfurt) — `https://verge-1-i4zv.onrender.com`
- **Frontend**: Vercel — `https://vergesignals.vercel.app`
- **Database**: Supabase (PostgreSQL)
- **Monitoring**: cron-job.org (hourly heartbeat trigger)

## Data Sources

| Source | What | Fallback Order |
|---|---|---|
| Binance | OHLCV candles (5m, 1h) | 1st |
| Coinbase Exchange | OHLCV candles (5m, no API key) | 2nd |
| CoinGecko | OHLC candles (30m, no volume) + spot price | 3rd |

## Graduation Gate

Real orders are never enabled by default. The system requires:

```
≥ 200 paper trades AND positive cumulative P&L
```

This is checked on every stats request. Until both conditions are met, the dashboard shows "Paper Trading" mode.

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

# Run
python app.py  # Starts on localhost:5001
```

Then open `frontend/index.html` in a browser. Set the API URL to `http://localhost:5001` in the console, or the app will prompt for the secret.

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

## Disclaimer

Verge is a **paper trading** decision-support tool. It does not place real bets. All trading decisions are your own. The indicators are based on technical analysis and do not guarantee outcomes. Crypto markets are volatile and unpredictable.
