"""End-to-end logic tests: generator → aggregation → analyzers.

These exercise the same code the Spark sinks call, without needing Kafka,
Spark or MongoDB — the parts that must be right are pure functions by design.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from loglens_common.aggregation import all_metrics, burstiness_from_span, prepare
from loglens_common.analyzers import analyze_endpoints, analyze_global, analyze_ips, dedupe
from loglens_common.schemas import LOG_FIELDS, AnomalyType

from generator.engine import TrafficEngine
from generator.scenarios import ScenarioScheduler

THRESHOLDS = {
    "zscore_threshold": 3.5,
    "ewma_alpha": 0.25,
    "error_rate_threshold": 0.08,
    "latency_p95_multiplier": 2.5,
    "ip_rps_threshold": 25,
    "scan_unique_paths": 30,
    "auth_fail_threshold": 15,
    "min_history_points": 12,
    "severity_low": 30,
    "severity_medium": 50,
    "severity_high": 70,
    "severity_critical": 85,
}

FIELD_NAMES = {name for name, _, _ in LOG_FIELDS}


def generate(minutes: float, rps: float = 70, seed: int = 5, scenarios=False, start=None):
    rng = random.Random(seed)
    scheduler = ScenarioScheduler(rng, enabled=False)
    engine = TrafficEngine(rng=rng, base_rps=rps, scheduler=scheduler)
    base = (start or datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)).timestamp()
    events = []
    step = 1.0
    for tick in range(int(minutes * 60 / step)):
        events.extend(engine.generate(base + tick * step, step))
    return events, engine, scheduler


# ── Generator ────────────────────────────────────────────────────────────────
def test_generated_events_match_the_declared_schema():
    events, _, _ = generate(0.5)
    assert len(events) > 100
    sample = events[0]
    assert FIELD_NAMES.issubset(set(sample)), FIELD_NAMES - set(sample)
    for event in events[:200]:
        assert 100 <= event["status"] <= 599
        assert event["response_time_ms"] > 0
        assert event["endpoint"].startswith("/")
        assert event["ip"].count(".") == 3


def test_traffic_follows_a_diurnal_shape():
    rng = random.Random(3)
    engine = TrafficEngine(rng=rng, base_rps=200, scheduler=None)
    night = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc).timestamp()
    afternoon = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc).timestamp()
    assert engine.target_rps(afternoon) > engine.target_rps(night) * 1.5


def test_sessions_produce_coherent_journeys():
    events, engine, _ = generate(1.0)
    frame = pd.DataFrame(events)
    multi = frame.groupby("session_id").size()
    assert (multi > 1).sum() > 20, "sessions should span multiple requests"
    assert engine.stats["organic"] == len(events)


# ── Aggregation ──────────────────────────────────────────────────────────────
def test_metric_documents_are_internally_consistent():
    events, _, _ = generate(2.0)
    metrics = all_metrics(pd.DataFrame(events))

    assert metrics["global"], "expected at least one window"
    total_windows = sum(doc["requests"] for doc in metrics["global"])
    assert total_windows == len(events)

    for doc in metrics["global"]:
        assert doc["requests"] > 0
        assert 0 <= doc["error_rate"] <= 1
        assert doc["p50_response_time"] <= doc["p95_response_time"] <= doc["p99_response_time"]
        assert doc["rps"] == pytest.approx(doc["requests"] / 60.0, abs=0.002)
        assert 0 <= doc["ip_gini"] <= 1
        assert 0 <= doc["path_entropy"] <= 1
        assert sum(doc["status_counts"].values()) == doc["requests"]

    for doc in metrics["endpoint"]:
        assert doc["errors_4xx"] + doc["errors_5xx"] <= doc["requests"]

    for doc in metrics["ip"]:
        assert doc["unique_paths"] >= 1
        assert 0 <= doc["error_ratio"] <= 1


def test_burstiness_definition_matches_the_streaming_formula():
    # 60 requests spread over a full minute vs. crammed into three seconds.
    assert burstiness_from_span(60, 60.0) == pytest.approx(1.0)
    assert burstiness_from_span(60, 3.0) == pytest.approx(20.0)
    assert burstiness_from_span(2, 0.0) == 1.0


def test_prepare_tolerates_missing_columns():
    frame = pd.DataFrame(
        [{"timestamp": "2026-08-18T12:00:00Z", "status": 200, "response_time_ms": 10.0}]
    )
    prepared = prepare(frame)
    assert prepared.iloc[0]["status_class"] == "2xx"
    assert prepared.iloc[0]["cache_hit"] in (True, False)


# ── Detection ────────────────────────────────────────────────────────────────
def _global_history(windows: int = 90, requests: int = 7200):
    start = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)
    rng = random.Random(11)
    return pd.DataFrame(
        [
            {
                "window_start": start + timedelta(minutes=i),
                "window_end": start + timedelta(minutes=i + 1),
                "requests": requests + rng.randint(-200, 200),
                "rps": requests / 60,
                "error_rate": 0.012 + rng.random() * 0.004,
                "server_error_rate": 0.003,
                "errors_4xx": 80,
                "errors_5xx": 20,
                "p95_response_time": 320 + rng.randint(-20, 20),
                "p99_response_time": 700,
                "unique_ips": 900,
                "unique_sessions": 1400,
                "bytes_per_request": 22_000,
                "ip_gini": 0.35,
                "path_entropy": 0.86,
                "bot_ratio": 0.06,
            }
            for i in range(windows)
        ]
    )


def test_global_traffic_spike_is_detected_and_explained():
    history = _global_history()
    current = history.iloc[-1].copy()
    current["window_start"] = history.iloc[-1]["window_start"] + timedelta(minutes=1)
    current["window_end"] = current["window_start"] + timedelta(minutes=1)
    current["requests"] = 45_000
    current["rps"] = 750

    found = analyze_global(current, history, THRESHOLDS)
    types = {a["type"] for a in found}
    assert AnomalyType.TRAFFIC_SPIKE in types

    spike = next(a for a in found if a["type"] == AnomalyType.TRAFFIC_SPIKE)
    assert spike["severity"] in ("high", "critical")
    assert spike["score"] > 70
    assert len(spike["contributing"]) >= 2, "multiple detectors should agree"
    assert "request volume" in spike["reason"]


def test_steady_traffic_produces_no_anomalies():
    history = _global_history()
    current = history.iloc[-1].copy()
    current["window_start"] = history.iloc[-1]["window_start"] + timedelta(minutes=1)
    current["window_end"] = current["window_start"] + timedelta(minutes=1)
    assert analyze_global(current, history.iloc[:-1], THRESHOLDS) == []


def test_ddos_signature_needs_concentration_not_just_volume():
    history = _global_history()
    base = history.iloc[-1].copy()
    base["window_start"] = history.iloc[-1]["window_start"] + timedelta(minutes=1)
    base["window_end"] = base["window_start"] + timedelta(minutes=1)
    base["requests"] = 30_000
    base["rps"] = 500

    spread = base.copy()  # high volume, normal shape → spike but not DDoS
    concentrated = base.copy()
    concentrated["ip_gini"] = 0.94
    concentrated["path_entropy"] = 0.12
    concentrated["unique_ips"] = 22

    spread_types = {a["type"] for a in analyze_global(spread, history, THRESHOLDS)}
    concentrated_types = {a["type"] for a in analyze_global(concentrated, history, THRESHOLDS)}
    assert AnomalyType.DDOS_BURST not in spread_types
    assert AnomalyType.DDOS_BURST in concentrated_types


def test_error_spike_reports_the_budget_breach():
    history = _global_history()
    current = history.iloc[-1].copy()
    current["window_start"] = history.iloc[-1]["window_start"] + timedelta(minutes=1)
    current["window_end"] = current["window_start"] + timedelta(minutes=1)
    current["error_rate"] = 0.42
    current["server_error_rate"] = 0.33
    current["errors_5xx"] = 2400

    found = [a for a in analyze_global(current, history, THRESHOLDS) if a["type"] == AnomalyType.ERROR_SPIKE]
    assert found
    assert found[0]["score"] > 60
    assert "%" in found[0]["reason"]


def test_latency_anomaly_uses_the_baseline_not_an_absolute():
    history = _global_history()
    current = history.iloc[-1].copy()
    current["window_start"] = history.iloc[-1]["window_start"] + timedelta(minutes=1)
    current["window_end"] = current["window_start"] + timedelta(minutes=1)
    current["p95_response_time"] = 2400

    found = [a for a in analyze_global(current, history, THRESHOLDS) if a["type"] == AnomalyType.LATENCY_ANOMALY]
    assert found
    assert found[0]["metrics"]["p95_response_time"] == 2400


def test_scanner_ip_is_classified_as_endpoint_scan():
    start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "window_start": start, "window_end": start + timedelta(minutes=1), "ip": f"10.0.0.{i}",
            "requests": 40, "unique_paths": 3, "unique_user_agents": 1, "error_ratio": 0.02,
            "not_found_ratio": 0.01, "auth_fail_count": 0, "avg_response_time": 180,
            "bytes_per_request": 20_000, "path_entropy": 0.4, "burstiness": 1.2,
            "bytes_sent": 800_000, "country": "US", "asn": "AS1", "org": "isp",
        }
        for i in range(40)
    ]
    rows.append(
        {
            "window_start": start, "window_end": start + timedelta(minutes=1), "ip": "45.9.13.7",
            "requests": 220, "unique_paths": 180, "unique_user_agents": 3, "error_ratio": 0.93,
            "not_found_ratio": 0.88, "auth_fail_count": 2, "avg_response_time": 30,
            "bytes_per_request": 400, "path_entropy": 0.97, "burstiness": 6.0,
            "bytes_sent": 88_000, "country": "RU", "asn": "AS49505", "org": "Selectel",
            "sample_paths": ["/.env", "/wp-admin/", "/.git/config"],
        }
    )
    found = analyze_ips(pd.DataFrame(rows), pd.DataFrame(), THRESHOLDS)
    scans = [a for a in found if a["type"] == AnomalyType.ENDPOINT_SCAN]
    assert scans, {a["type"] for a in found}
    assert scans[0]["entity"] == "45.9.13.7"
    assert "404" in scans[0]["reason"]
    # The forty benign clients must not be flagged.
    assert {a["entity"] for a in found} == {"45.9.13.7"}


def test_credential_stuffing_is_separated_from_scanning():
    start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    row = {
        "window_start": start, "window_end": start + timedelta(minutes=1), "ip": "185.220.4.9",
        "requests": 90, "unique_paths": 1, "unique_user_agents": 1, "error_ratio": 0.95,
        "not_found_ratio": 0.0, "auth_fail_count": 84, "avg_response_time": 210,
        "bytes_per_request": 900, "path_entropy": 0.0, "burstiness": 3.0,
        "bytes_sent": 81_000, "country": "BG", "asn": "AS203380", "org": "hoster",
    }
    found = analyze_ips(pd.DataFrame([row]), pd.DataFrame(), THRESHOLDS)
    assert AnomalyType.AUTH_ABUSE in {a["type"] for a in found}


def test_slow_endpoint_detected_against_its_own_baseline():
    start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    history = pd.DataFrame(
        [
            {
                "window_start": start - timedelta(minutes=i + 1),
                "window_end": start - timedelta(minutes=i),
                "endpoint": "/api/v1/checkout", "method": "POST", "service": "checkout-svc",
                "requests": 300, "errors_4xx": 4, "errors_5xx": 1, "error_rate": 0.016,
                "p50_response_time": 240, "p95_response_time": 380, "p99_response_time": 600,
                "apdex": 0.96, "unique_ips": 200,
            }
            for i in range(40)
        ]
    )
    current = pd.DataFrame(
        [
            {
                "window_start": start, "window_end": start + timedelta(minutes=1),
                "endpoint": "/api/v1/checkout", "method": "POST", "service": "checkout-svc",
                "requests": 300, "errors_4xx": 4, "errors_5xx": 1, "error_rate": 0.016,
                "p50_response_time": 1800, "p95_response_time": 3400, "p99_response_time": 5200,
                "apdex": 0.31, "unique_ips": 200,
            }
        ]
    )
    found = analyze_endpoints(current, history, THRESHOLDS)
    slow = [a for a in found if a["type"] == AnomalyType.SLOW_ENDPOINT]
    assert slow
    assert slow[0]["entity"] == "/api/v1/checkout"
    assert slow[0]["metrics"]["p95_response_time"] == 3400


def test_dedupe_keeps_the_strongest_per_identity():
    start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    history = _global_history()
    current = history.iloc[-1].copy()
    current["window_start"] = start
    current["window_end"] = start + timedelta(minutes=1)
    current["requests"] = 60_000
    duplicated = analyze_global(current, history, THRESHOLDS) * 3
    assert len(dedupe(duplicated)) == len(dedupe(dedupe(duplicated)))
    assert len({a["anomaly_id"] for a in dedupe(duplicated)}) == len(dedupe(duplicated))


# ── Scenarios ────────────────────────────────────────────────────────────────
def test_injected_ddos_is_labelled_and_changes_the_metrics():
    rng = random.Random(21)
    scheduler = ScenarioScheduler(rng, enabled=False)
    engine = TrafficEngine(rng=rng, base_rps=60, scheduler=scheduler)
    base = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc).timestamp()

    calm = []
    for tick in range(60):
        calm.extend(engine.generate(base + tick, 1.0))

    scheduler.spawn(base + 60, "ddos_burst", intensity=1.5, duration=120, source="test")
    attacked = []
    for tick in range(60, 140):
        attacked.extend(engine.generate(base + tick, 1.0))

    labels = {event["attack_label"] for event in attacked if event["attack_label"]}
    assert "ddos_burst" in labels

    calm_metrics = all_metrics(pd.DataFrame(calm))["global"]
    attack_metrics = all_metrics(pd.DataFrame(attacked))["global"]
    calm_peak = max(doc["requests"] for doc in calm_metrics)
    attack_peak = max(doc["requests"] for doc in attack_metrics)
    attack_gini = max(doc["ip_gini"] for doc in attack_metrics)
    calm_gini = max(doc["ip_gini"] for doc in calm_metrics)

    assert attack_peak > calm_peak
    assert attack_gini > calm_gini


def test_scenario_envelope_ramps_and_decays():
    rng = random.Random(4)
    scheduler = ScenarioScheduler(rng, enabled=False)
    scenario = scheduler.spawn(1000.0, "error_spike", intensity=1.0, duration=100)
    assert scenario is not None
    assert scenario.envelope(1000.0) == pytest.approx(0.0, abs=0.01)
    assert scenario.envelope(1050.0) == pytest.approx(1.0, abs=0.01)
    assert scenario.envelope(1099.0) < 0.2
    assert not scenario.active(1101.0)
