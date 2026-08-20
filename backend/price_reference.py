"""15m price-reference quality and audit helpers.

This module keeps strike, current reference, and resolution diagnostics
separate. It does not decide trading edge; it only classifies source quality
and explains price-reference health.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, asdict


QUALITY_HIGH = "HIGH"
QUALITY_GOOD = "GOOD"
QUALITY_DEGRADED = "DEGRADED"
QUALITY_FALLBACK = "FALLBACK"
QUALITY_INVALID = "INVALID"

REFERENCE_WARN_BPS = float(os.environ.get("REFERENCE_WARN_BPS", "5"))
REFERENCE_CRITICAL_BPS = float(os.environ.get("REFERENCE_CRITICAL_BPS", "15"))
STALE_REFERENCE_MS = int(os.environ.get("REFERENCE_STALE_MS", "15000"))
MAX_GAP_GOOD_MS = int(os.environ.get("REFERENCE_MAX_GAP_GOOD_MS", "5000"))
MAX_GAP_DEGRADED_MS = int(os.environ.get("REFERENCE_MAX_GAP_DEGRADED_MS", "15000"))
MIN_OBSERVATIONS_60S = int(os.environ.get("REFERENCE_MIN_OBSERVATIONS_60S", "3"))


@dataclass
class ObservationHealth:
    status: str
    observation_count: int
    expected_observation_count: int
    largest_gap_seconds: float | None
    stale_observation_count: int
    duplicate_observation_count: int
    out_of_order_count: int
    latest_age_ms: int | None
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def classify_strike_source(source: str | None, value: float | None) -> str:
    if value is None or value <= 0:
        return QUALITY_INVALID
    if source in {"polymarket_price_to_beat", "gamma_price_to_beat"}:
        return QUALITY_HIGH
    if source in {"rtds_chainlink_twap_60s", "rtds_chainlink_tick"}:
        return QUALITY_GOOD
    if source in {"chainlink_onchain_twap_60s", "chainlink_onchain_tick", "chainlink_ws"}:
        return QUALITY_FALLBACK
    if source in {"binance_candle", "coinbase_spot", "candle_close", "gamma_recursive_search", "gamma_text_parse"}:
        return QUALITY_FALLBACK
    return QUALITY_INVALID


def classify_reference_source(source: str | None, value: float | None, health: ObservationHealth | None = None) -> str:
    if value is None or value <= 0:
        return QUALITY_INVALID
    if source in {"rtds_chainlink", "polymarket_rtds_60s_twap_estimate", "rtds_chainlink_twap_60s"}:
        return health.status if health else QUALITY_GOOD
    if source in {"chainlink_ws", "chainlink_onchain"}:
        return QUALITY_FALLBACK
    if source in {"coinbase_spot", "candle_close", "binance_candle"}:
        return QUALITY_FALLBACK
    return QUALITY_DEGRADED


def assess_observation_health(
    ticks: list[dict],
    now_ms: int | None = None,
    window_ms: int = 60_000,
) -> ObservationHealth:
    now_ms = now_ms or int(time.time() * 1000)
    cutoff_ms = now_ms - window_ms
    normalized = []
    for tick in ticks:
        ts = tick.get("timestamp_ms")
        price = tick.get("price")
        try:
            ts_int = int(ts)
            price_float = float(price)
        except (TypeError, ValueError):
            continue
        if ts_int >= cutoff_ms and ts_int <= now_ms:
            normalized.append({"timestamp_ms": ts_int, "price": price_float})

    reasons = []
    if not normalized:
        return ObservationHealth(
            status=QUALITY_INVALID,
            observation_count=0,
            expected_observation_count=MIN_OBSERVATIONS_60S,
            largest_gap_seconds=None,
            stale_observation_count=0,
            duplicate_observation_count=0,
            out_of_order_count=0,
            latest_age_ms=None,
            reasons=["no_observations"],
        )

    duplicate_count = 0
    seen = set()
    for tick in normalized:
        key = (tick["timestamp_ms"], tick["price"])
        if key in seen:
            duplicate_count += 1
        seen.add(key)

    out_of_order_count = 0
    prev = None
    for tick in normalized:
        ts = tick["timestamp_ms"]
        if prev is not None and ts < prev:
            out_of_order_count += 1
        prev = ts

    ordered = sorted(normalized, key=lambda t: t["timestamp_ms"])
    gaps = [ordered[i]["timestamp_ms"] - ordered[i - 1]["timestamp_ms"] for i in range(1, len(ordered))]
    largest_gap_ms = max(gaps) if gaps else None
    latest_age_ms = now_ms - ordered[-1]["timestamp_ms"]
    stale_count = sum(1 for tick in ordered if now_ms - tick["timestamp_ms"] > STALE_REFERENCE_MS)

    status = QUALITY_GOOD
    if len(ordered) < MIN_OBSERVATIONS_60S:
        status = QUALITY_DEGRADED
        reasons.append("insufficient_sample_density")
    if latest_age_ms > STALE_REFERENCE_MS:
        status = QUALITY_INVALID
        reasons.append("latest_observation_stale")
    if largest_gap_ms is not None and largest_gap_ms > MAX_GAP_DEGRADED_MS:
        status = QUALITY_DEGRADED if status != QUALITY_INVALID else status
        reasons.append("large_time_gap")
    elif largest_gap_ms is not None and largest_gap_ms > MAX_GAP_GOOD_MS:
        status = QUALITY_DEGRADED if status != QUALITY_INVALID else status
        reasons.append("limited_gap")
    if duplicate_count:
        status = QUALITY_DEGRADED if status != QUALITY_INVALID else status
        reasons.append("duplicate_observations")
    if out_of_order_count:
        status = QUALITY_DEGRADED if status != QUALITY_INVALID else status
        reasons.append("out_of_order_observations")

    return ObservationHealth(
        status=status,
        observation_count=len(ordered),
        expected_observation_count=MIN_OBSERVATIONS_60S,
        largest_gap_seconds=round(largest_gap_ms / 1000, 3) if largest_gap_ms is not None else None,
        stale_observation_count=stale_count,
        duplicate_observation_count=duplicate_count,
        out_of_order_count=out_of_order_count,
        latest_age_ms=latest_age_ms,
        reasons=reasons,
    )


def compare_prices(primary: float | None, comparison: float | None) -> dict:
    if primary is None or comparison is None or comparison <= 0:
        return {
            "absolute_difference": None,
            "difference_percent": None,
            "difference_bps": None,
            "status": QUALITY_INVALID,
        }
    diff = float(primary) - float(comparison)
    pct = diff / float(comparison) * 100
    bps = pct * 100
    status = QUALITY_GOOD
    if abs(bps) >= REFERENCE_CRITICAL_BPS:
        status = "CRITICAL"
    elif abs(bps) >= REFERENCE_WARN_BPS:
        status = "WARNING"
    return {
        "absolute_difference": round(diff, 8),
        "difference_percent": round(pct, 6),
        "difference_bps": round(bps, 4),
        "status": status,
        "warn_bps": REFERENCE_WARN_BPS,
        "critical_bps": REFERENCE_CRITICAL_BPS,
    }


def build_reference_audit(
    market_id: str | None,
    window_start: int | None,
    window_end: int | None,
    price_to_beat: float | None,
    price_to_beat_source: str | None,
    current_reference: float | None,
    current_reference_source: str | None,
    reference_health: ObservationHealth | None,
    opening_reference: float | None = None,
    opening_reference_source: str | None = None,
) -> dict:
    strike_quality = classify_strike_source(price_to_beat_source, price_to_beat)
    reference_quality = classify_reference_source(current_reference_source, current_reference, reference_health)
    comparison = compare_prices(current_reference, price_to_beat)
    fallback_used = strike_quality == QUALITY_FALLBACK or reference_quality == QUALITY_FALLBACK

    return {
        "market_id": market_id,
        "window_start": window_start,
        "window_end": window_end,
        "price_to_beat": price_to_beat,
        "price_to_beat_source": price_to_beat_source,
        "price_to_beat_quality": strike_quality,
        "opening_reference": opening_reference,
        "opening_reference_source": opening_reference_source,
        "opening_reference_quality": classify_reference_source(opening_reference_source, opening_reference, reference_health),
        "current_reference": current_reference,
        "current_reference_source": current_reference_source,
        "current_reference_quality": reference_quality,
        "current_reference_age": reference_health.latest_age_ms if reference_health else None,
        "difference": comparison["absolute_difference"],
        "difference_percent": comparison["difference_percent"],
        "difference_bps": comparison["difference_bps"],
        "discrepancy_status": comparison["status"],
        "fallback_used": fallback_used,
        "fallback_reason": "; ".join(reference_health.reasons) if reference_health and reference_health.reasons else None,
        "observation_count": reference_health.observation_count if reference_health else 0,
        "expected_observation_count": reference_health.expected_observation_count if reference_health else MIN_OBSERVATIONS_60S,
        "largest_gap_seconds": reference_health.largest_gap_seconds if reference_health else None,
        "stale_observation_count": reference_health.stale_observation_count if reference_health else 0,
        "duplicate_observation_count": reference_health.duplicate_observation_count if reference_health else 0,
        "out_of_order_timestamp_count": reference_health.out_of_order_count if reference_health else 0,
        "thresholds": {
            "reference_warn_bps": REFERENCE_WARN_BPS,
            "reference_critical_bps": REFERENCE_CRITICAL_BPS,
        },
    }
