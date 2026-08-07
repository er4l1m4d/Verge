"""Market Configuration — per-duration settings for 1h and 15m markets.

Adding a third duration later means adding one entry here, not hunting
through five files. Each duration entry describes: window size, data source,
indicator parameters, and risk rules.

The "1h" entry reproduces the exact hardcoded values from indicators.py
and engine.py so nothing shifts under existing trades.
"""

# ---------------------------------------------------------------------------
# Per-duration configuration
# ---------------------------------------------------------------------------

MARKET_CONFIG = {
    # -----------------------------------------------------------------------
    # 1-hour — current behavior, values frozen from PRD §6.2–6.4
    # -----------------------------------------------------------------------
    "1h": {
        # Market discovery
        "window_ms": 3_600_000,                    # 60 minutes
        "series_slug": "btc-up-or-down-hourly",    # Gamma API filter
        "slug_prefix": "btc-updown-hourly-",       # fallback discovery

        # Price source
        "price_source": "binance",                 # Binance klines
        "bar_interval": "5m",                      # 5-minute candles
        "bar_lookback": 50,                        # 50 candles = 250 min lookback

        # Indicator parameters (frozen from indicators.py defaults)
        "rsi_period": 14,                          # RSI_PERIOD = 14
        "ma_fast": 5,                              # MA_FAST = 5  (≈25 min on 5m bars)
        "ma_slow": 15,                             # MA_SLOW = 15 (≈75 min on 5m bars)

        # Volume
        "volume_lookback": 10,                     # 10-period avg for spike detection
        # Note: VOLUME_SPIKE_RATIO = 3.0 is in indicators.py (PRD-locked)

        # Risk
        "no_bet_final_minutes": 10,                # PRD §7: no bets in final 10 min
        "suggested_price_discount": 0.95,          # 5% below current odds

        # Minimum candles needed for a valid signal
        "min_candles": 16,                         # engine.py: len(df_5m) < 16 → SKIP
    },

    # -----------------------------------------------------------------------
    # 15-minute — new duration, indicators on 1-minute bars
    # -----------------------------------------------------------------------
    "15m": {
        # Market discovery
        "window_ms": 900_000,                      # 15 minutes
        "series_slug": "btc-up-or-down-15m",       # Gamma API filter (verified Phase 1)
        "slug_prefix": "btc-updown-15m-",          # fallback discovery

        # Price source
        "price_source": "chainlink",               # on-chain Data Feed (Phase 3)
        "bar_interval": "1m",                      # 1-minute bars
        "bar_lookback": 20,                        # 20 candles for enough history

        # Indicator parameters — sized for 1-minute bars inside a 15m window
        # RSI-14 on 1m bars covers 14 minutes (fits inside the 15m window)
        "rsi_period": 14,
        # MA-5 covers 5 minutes, MA-15 covers the full 15m window
        "ma_fast": 5,
        "ma_slow": 15,

        # Volume
        "volume_lookback": 10,                     # same lookback as 1h
        # Volume is proxied from Binance BTC/USDT (see Phase 3.4)

        # Risk — tighter than 1h because the window is 4x shorter
        "no_bet_final_minutes": 3,                 # ~20% of window (vs ~17% for 1h)
        "suggested_price_discount": 0.95,          # same discount as 1h

        # Minimum candles needed for a valid signal
        "min_candles": 16,                         # need at least 16 1m candles
    },
}


# ---------------------------------------------------------------------------
# PRD-locked constants (not duration-dependent)
# These are the same across ALL durations — Polymarket's fee structure
# and scoring weights don't change with market length.
# ---------------------------------------------------------------------------

# Scoring weights (PRD §6.2)
WEIGHT_RSI = 0.40
WEIGHT_VOLUME = 0.35
WEIGHT_MA = 0.25

# Score thresholds (PRD §6.3)
SCORE_HIGH_THRESHOLD = 0.6
SCORE_LOW_THRESHOLD = 0.4

# Fee model (PRD §6.4)
TAKER_FEE_RATE = 0.07              # 7% taker fee
MIN_VIABLE_EDGE_PCT = 3.0          # below this after fees → SKIP

# Model probability mapping
MODEL_PROB_SLOPE = 0.15            # model_prob = 0.5 + score * 0.15


def get_config(duration: str) -> dict:
    """Get the config entry for a given duration.

    Raises KeyError if duration is not configured.
    """
    if duration not in MARKET_CONFIG:
        raise ValueError(
            f"Unknown duration '{duration}'. "
            f"Available: {list(MARKET_CONFIG.keys())}"
        )
    return MARKET_CONFIG[duration]


def supported_durations() -> list[str]:
    """Return list of supported duration keys."""
    return list(MARKET_CONFIG.keys())
