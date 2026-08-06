"""Indicator & Scoring Engine — Phase 2.

Pure functions, no network calls. This is the single source of truth for
signal generation — both the backtest and the live engine call these.
"""
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Constants (locked from PRD §6.2–6.4)
# ---------------------------------------------------------------------------

RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

MA_FAST = 5    # 5-minute bars ≈ 25 minutes
MA_SLOW = 15   # 15-minute bars ≈ 75 minutes

VOLUME_SPIKE_RATIO = 3.0

WEIGHT_RSI = 0.40
WEIGHT_VOLUME = 0.35
WEIGHT_MA = 0.25

SCORE_HIGH_THRESHOLD = 0.6
SCORE_LOW_THRESHOLD = 0.4

TAKER_FEE_RATE = 0.07
MIN_VIABLE_EDGE_PCT = 3.0  # percent — fee erodes below this → SKIP

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class SignalResult(NamedTuple):
    score: float
    decision: str      # "BET HIGHER", "BET LOWER", "SKIP"
    confidence: str    # "High", "Low", or ""
    model_probability: float  # indicator-implied probability (0–1)


class EdgeResult(NamedTuple):
    raw_decision: str
    final_decision: str
    edge_pct: float
    fee: float
    fee_eroded: bool

# ---------------------------------------------------------------------------
# 2.1 — RSI (Wilder's smoothing)
# ---------------------------------------------------------------------------


def calculate_rsi(prices: list[float], period: int = RSI_PERIOD) -> float:
    """Wilder's RSI — exponential moving average of gains/losses.

    Returns a value between 0 and 100.
    If fewer than `period + 1` data points, returns 50.0 (neutral).
    """
    if len(prices) < period + 1:
        return 50.0

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    # Initial average: simple mean of first `period` deltas
    gains = [d if d > 0 else 0.0 for d in deltas[:period]]
    losses = [-d if d < 0 else 0.0 for d in deltas[:period]]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder's smoothing for remaining deltas
    for d in deltas[period:]:
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0 and avg_gain == 0:
        return 50.0  # flat — no movement
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

# ---------------------------------------------------------------------------
# 2.2 — MA crossover
# ---------------------------------------------------------------------------


def ma_crossover(
    prices: list[float],
    fast: int = MA_FAST,
    slow: int = MA_SLOW,
) -> int:
    """Simple moving average crossover signal.

    Returns +1 if fast > slow (uptrend), -1 if fast < slow (downtrend),
    0 if equal or insufficient data.
    """
    if len(prices) < slow:
        return 0

    fast_ma = sum(prices[-fast:]) / fast
    slow_ma = sum(prices[-slow:]) / slow

    if fast_ma > slow_ma:
        return 1
    elif fast_ma < slow_ma:
        return -1
    return 0

# ---------------------------------------------------------------------------
# 2.3 — Volume spike
# ---------------------------------------------------------------------------


def volume_spike(
    current_volume: float,
    avg_volume: float,
    direction: int,
) -> int:
    """Volume spike detector.

    Returns +1 if volume ≥ 3x average in the upward direction,
    -1 if ≥ 3x in the downward direction, 0 otherwise.
    `direction` should be +1 (up candle) or -1 (down candle).
    """
    if avg_volume <= 0 or current_volume < VOLUME_SPIKE_RATIO * avg_volume:
        return 0
    return direction

# ---------------------------------------------------------------------------
# 2.4 — Weighted scoring
# ---------------------------------------------------------------------------


def score_signal(rsi_val: float, ma_val: int, volume_val: int) -> SignalResult:
    """Combine indicators into a weighted score and decision.

    Weights: RSI 40%, Volume 35%, MA 25% (locked in PRD §6.2).
    Thresholds: ±0.6 → High confidence, ±0.4 → Low confidence, else SKIP.

    model_probability maps the score to a 0–1 range:
    score=0 → 0.5, score=+1 → ~0.65, score=-1 → ~0.35.
    """
    rsi_component = 0.0
    if rsi_val > RSI_OVERBOUGHT:
        rsi_component = -1.0  # overbought → bet lower
    elif rsi_val < RSI_OVERSOLD:
        rsi_component = 1.0   # oversold → bet higher

    score = (
        rsi_component * WEIGHT_RSI
        + volume_val * WEIGHT_VOLUME
        + ma_val * WEIGHT_MA
    )

    # Map score to model probability (centered at 0.5)
    model_prob = 0.5 + score * 0.15

    if score >= SCORE_HIGH_THRESHOLD:
        return SignalResult(score, "BET HIGHER", "High", model_prob)
    elif score >= SCORE_LOW_THRESHOLD:
        return SignalResult(score, "BET HIGHER", "Low", model_prob)
    elif score <= -SCORE_HIGH_THRESHOLD:
        return SignalResult(score, "BET LOWER", "High", model_prob)
    elif score <= -SCORE_LOW_THRESHOLD:
        return SignalResult(score, "BET LOWER", "Low", model_prob)
    return SignalResult(score, "SKIP", "", model_prob)

# ---------------------------------------------------------------------------
# 2.5 — Fee-adjusted edge
# ---------------------------------------------------------------------------


def fee_adjusted_edge(
    decision: str,
    odds_price: float,
    model_probability: float,
    shares: float = 100.0,
) -> EdgeResult:
    """Compute fee-adjusted edge and potentially downgrade decision to SKIP.

    `model_probability` is the indicator-implied probability (0–1) that the
    outcome will happen. `odds_price` is the current market price (0–1).
    Edge = (model_probability - odds_price) - fee_rate, where fee_rate
    peaks at 1.75% (at 50¢) and shrinks toward the extremes.

    If the raw edge (before fee) is below MIN_VIABLE_EDGE_PCT, or if the
    fee erodes it below that threshold, decision → SKIP.
    """
    if decision == "SKIP":
        return EdgeResult("SKIP", "SKIP", 0.0, 0.0, False)

    # Fee per dollar of potential profit (normalized)
    fee_rate = TAKER_FEE_RATE * odds_price * (1 - odds_price)

    if decision == "BET HIGHER":
        raw_edge = (model_probability - odds_price) - fee_rate
    else:
        raw_edge = ((1 - model_probability) - (1 - odds_price)) - fee_rate

    edge_pct = raw_edge * 100
    fee = shares * fee_rate

    fee_eroded = False
    final_decision = decision
    if edge_pct < MIN_VIABLE_EDGE_PCT:
        fee_eroded = True
        final_decision = "SKIP"

    return EdgeResult(decision, final_decision, edge_pct, fee, fee_eroded)
