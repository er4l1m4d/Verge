import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from price_reference import (
    QUALITY_DEGRADED,
    QUALITY_FALLBACK,
    QUALITY_GOOD,
    QUALITY_HIGH,
    QUALITY_INVALID,
    assess_observation_health,
    build_reference_audit,
    classify_reference_source,
    classify_strike_source,
    compare_prices,
)


def test_price_to_beat_source_is_high_quality():
    assert classify_strike_source("polymarket_price_to_beat", 116482.31) == QUALITY_HIGH


def test_missing_malformed_or_non_positive_price_to_beat_is_invalid():
    assert classify_strike_source("polymarket_price_to_beat", None) == QUALITY_INVALID
    assert classify_strike_source("polymarket_price_to_beat", 0) == QUALITY_INVALID
    assert classify_strike_source("polymarket_price_to_beat", -1) == QUALITY_INVALID


def test_fallback_strike_is_not_high_quality():
    assert classify_strike_source("chainlink_onchain_tick", 116482.31) == QUALITY_FALLBACK
    assert classify_strike_source("binance_candle", 116482.31) == QUALITY_FALLBACK


def test_fresh_rtds_observations_are_good():
    now = 1_000_000
    ticks = [
        {"timestamp_ms": now - 4000, "price": 100.0},
        {"timestamp_ms": now - 2000, "price": 101.0},
        {"timestamp_ms": now - 500, "price": 102.0},
    ]
    health = assess_observation_health(ticks, now_ms=now)
    assert health.status == QUALITY_GOOD
    assert health.observation_count == 3
    assert health.largest_gap_seconds == 2.0


def test_stale_observations_are_invalid():
    now = 1_000_000
    ticks = [
        {"timestamp_ms": now - 55_000, "price": 100.0},
        {"timestamp_ms": now - 45_000, "price": 101.0},
        {"timestamp_ms": now - 30_000, "price": 102.0},
    ]
    health = assess_observation_health(ticks, now_ms=now)
    assert health.status == QUALITY_INVALID
    assert "latest_observation_stale" in health.reasons


def test_gaps_and_duplicates_are_degraded():
    now = 1_000_000
    ticks = [
        {"timestamp_ms": now - 50_000, "price": 100.0},
        {"timestamp_ms": now - 30_000, "price": 101.0},
        {"timestamp_ms": now - 30_000, "price": 101.0},
        {"timestamp_ms": now - 500, "price": 102.0},
    ]
    health = assess_observation_health(ticks, now_ms=now)
    assert health.status == QUALITY_DEGRADED
    assert health.duplicate_observation_count == 1
    assert "large_time_gap" in health.reasons


def test_out_of_order_observations_are_degraded():
    now = 1_000_000
    ticks = [
        {"timestamp_ms": now - 1000, "price": 102.0},
        {"timestamp_ms": now - 3000, "price": 101.0},
        {"timestamp_ms": now - 500, "price": 103.0},
    ]
    health = assess_observation_health(ticks, now_ms=now)
    assert health.status == QUALITY_DEGRADED
    assert health.out_of_order_count == 1


def test_reference_source_uses_health_for_rtds():
    now = 1_000_000
    health = assess_observation_health(
        [
            {"timestamp_ms": now - 1000, "price": 100.0},
            {"timestamp_ms": now - 500, "price": 101.0},
            {"timestamp_ms": now - 100, "price": 102.0},
        ],
        now_ms=now,
    )
    assert classify_reference_source("rtds_chainlink", 102.0, health) == QUALITY_GOOD
    assert classify_reference_source("chainlink_onchain", 102.0, health) == QUALITY_FALLBACK
    assert classify_reference_source("rtds_chainlink", None, health) == QUALITY_INVALID


def test_discrepancy_thresholds_return_warning_or_critical():
    warning = compare_prices(100.06, 100.0)
    critical = compare_prices(100.20, 100.0)
    assert warning["status"] == "WARNING"
    assert critical["status"] == "CRITICAL"


def test_reference_audit_separates_strike_and_current_reference():
    now = 1_000_000
    health = assess_observation_health(
        [
            {"timestamp_ms": now - 1000, "price": 100.0},
            {"timestamp_ms": now - 500, "price": 100.1},
            {"timestamp_ms": now - 100, "price": 100.2},
        ],
        now_ms=now,
    )
    audit = build_reference_audit(
        market_id="m1",
        window_start=900_000,
        window_end=1_800_000,
        price_to_beat=100.0,
        price_to_beat_source="polymarket_price_to_beat",
        current_reference=100.2,
        current_reference_source="rtds_chainlink",
        reference_health=health,
    )
    assert audit["price_to_beat_quality"] == QUALITY_HIGH
    assert audit["current_reference_quality"] == QUALITY_GOOD
    assert audit["difference"] == 0.2
    assert audit["fallback_used"] is False
