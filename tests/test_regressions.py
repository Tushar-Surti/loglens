"""Regressions for defects found while running the platform end to end.

Each test here corresponds to a bug that actually reached a running container.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from loglens_common.analyzers import analyze_endpoints, analyze_ips
from loglens_common.detectors import fuse, mahalanobis_detector, robust_zscore_detector
from loglens_common.mongo import bson_safe
from loglens_common.schemas import AnomalyType, Detector

THRESHOLDS = {
    "zscore_threshold": 3.5,
    "ip_rps_threshold": 25,
    "scan_unique_paths": 30,
    "auth_fail_threshold": 15,
    "severity_low": 30,
    "severity_medium": 50,
    "severity_high": 70,
    "severity_critical": 85,
}

START = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _ip_row(ip: str, **overrides):
    row = {
        "window_start": START,
        "window_end": START + timedelta(minutes=1),
        "ip": ip,
        "requests": 40,
        "unique_paths": 3,
        "unique_user_agents": 1,
        "error_ratio": 0.02,
        "not_found_ratio": 0.0,
        "auth_fail_count": 0,
        "avg_response_time": 180.0,
        "bytes_per_request": 20_000.0,
        "path_entropy": 0.45,
        "burstiness": 1.2,
        "bytes_sent": 800_000,
        "country": "US",
        "asn": "AS14618",
        "org": "Amazon-AES",
    }
    row.update(overrides)
    return row


# ── BSON encoding ────────────────────────────────────────────────────────────
def test_numpy_bool_is_encodable_after_sanitising():
    """`np.bool_` is not a `bool` subclass and BSON rejects it.

    One such value buried in an evidence dict failed an entire bulk write.
    """
    assert not isinstance(np.bool_(True), bool)

    payload = {
        "triggered": np.bool_(True),
        "score": np.float64(91.5),
        "count": np.int64(12),
        "nested": {"flags": [np.bool_(False), np.bool_(True)]},
        "array": np.array([1.5, 2.5]),
        "bad_float": float("nan"),
    }
    clean = bson_safe(payload)

    assert clean["triggered"] is True
    assert isinstance(clean["score"], float)
    assert isinstance(clean["count"], int)
    assert clean["nested"]["flags"] == [False, True]
    assert clean["array"] == [1.5, 2.5]
    assert clean["bad_float"] is None
    # Nothing NumPy-typed may survive.
    assert all(not isinstance(v, np.generic) for v in clean["nested"]["flags"])


def test_detector_triggered_flag_is_a_native_bool():
    result = robust_zscore_detector([100.0] * 40, 5000.0, direction="up")
    assert result.triggered is True
    assert isinstance(result.triggered, bool)


# ── Detector identity ────────────────────────────────────────────────────────
def test_mahalanobis_is_not_labelled_as_isolation_forest():
    """Two correlated views of the same statistic must not 'agree'.

    Mahalanobis previously reported itself as ``isolation_forest``, so fusion
    counted the pair as two independent detectors and inflated borderline
    scores into the critical band.
    """
    history = pd.DataFrame(
        {
            "requests": np.random.default_rng(0).normal(50, 8, 60),
            "error_ratio": np.random.default_rng(1).normal(0.02, 0.005, 60),
        }
    )
    row = pd.Series({"requests": 400.0, "error_ratio": 0.9})
    result = mahalanobis_detector(history, row, ["requests", "error_ratio"])

    assert result.detector == Detector.MAHALANOBIS
    assert result.detector != Detector.ISOLATION_FOREST


def test_correlated_detectors_do_not_double_count_as_agreement():
    class R:
        def __init__(self, detector, score):
            self.detector, self.score, self.evidence = detector, score, {}
            self.triggered, self.reason = score > 0.5, ""

    same_method_twice = fuse([R(Detector.ISOLATION_FOREST, 0.8), R(Detector.ISOLATION_FOREST, 0.8)])
    distinct_methods = fuse([R(Detector.ISOLATION_FOREST, 0.8), R(Detector.MAHALANOBIS, 0.8)])

    # Distinct detector names are now visible as distinct evidence.
    assert len(set(distinct_methods["detectors"])) == 2
    assert len(set(same_method_twice["detectors"])) == 1


# ── Volume floor ─────────────────────────────────────────────────────────────
def test_low_volume_outliers_are_not_flagged():
    """A 5-request client with a 100% error ratio is noise, not a threat.

    It is a genuine statistical outlier in a heavy-tailed population, which is
    exactly why the model-only catch-all needs a volume floor.
    """
    rows = [_ip_row(f"10.0.0.{i}") for i in range(60)]
    rows.append(
        _ip_row("35.176.68.114", requests=5, error_ratio=1.0, unique_paths=1, path_entropy=0.0,
                bytes_per_request=300.0, bytes_sent=1_500, avg_response_time=12.0)
    )
    found = analyze_ips(pd.DataFrame(rows), pd.DataFrame(), THRESHOLDS)
    assert "35.176.68.114" not in {a["entity"] for a in found}


def test_high_volume_attacker_is_still_flagged():
    """The floor must not suppress the thing we exist to catch."""
    rows = [_ip_row(f"10.0.0.{i}") for i in range(60)]
    rows.append(
        _ip_row("45.9.105.39", requests=2600, unique_paths=2, error_ratio=0.35,
                path_entropy=0.05, burstiness=14.0, bytes_per_request=2_400.0,
                bytes_sent=6_240_000, country="RU", org="Selectel")
    )
    found = analyze_ips(pd.DataFrame(rows), pd.DataFrame(), THRESHOLDS)
    flagged = [a for a in found if a["entity"] == "45.9.105.39"]

    assert flagged, "the volumetric attacker must still be detected"
    assert flagged[0]["type"] in (AnomalyType.DDOS_BURST, AnomalyType.SUSPICIOUS_IP)
    assert flagged[0]["severity"] in ("high", "critical")


def test_benign_population_produces_no_detections():
    rows = [_ip_row(f"10.0.0.{i}", requests=35 + i % 12) for i in range(80)]
    assert analyze_ips(pd.DataFrame(rows), pd.DataFrame(), THRESHOLDS) == []


# ── Materiality gates ────────────────────────────────────────────────────────
def _endpoint_history(endpoint: str, p95: float, error_rate: float, requests: int = 400, n: int = 40,
                      server_error_rate: float = 0.0):
    return pd.DataFrame(
        [
            {
                "window_start": START - timedelta(minutes=i + 1),
                "window_end": START - timedelta(minutes=i),
                "endpoint": endpoint,
                "method": "GET",
                "service": "api-gateway",
                "requests": requests,
                "errors_4xx": int(requests * max(error_rate - server_error_rate, 0)),
                "errors_5xx": int(requests * server_error_rate),
                "error_rate": error_rate,
                "server_error_rate": server_error_rate,
                "p50_response_time": p95 * 0.5,
                "p95_response_time": p95,
                "p99_response_time": p95 * 1.4,
                "apdex": 0.99,
                "unique_ips": 200,
            }
            for i in range(n)
        ]
    )


def _endpoint_now(endpoint: str, p95: float, error_rate: float, requests: int,
                  server_error_rate: float = 0.0):
    return pd.DataFrame(
        [
            {
                "window_start": START,
                "window_end": START + timedelta(minutes=1),
                "endpoint": endpoint,
                "method": "GET",
                "service": "api-gateway",
                "requests": requests,
                "errors_4xx": int(requests * max(error_rate - server_error_rate, 0)),
                "errors_5xx": int(requests * server_error_rate),
                "error_rate": error_rate,
                "server_error_rate": server_error_rate,
                "p50_response_time": p95 * 0.5,
                "p95_response_time": p95,
                "p99_response_time": p95 * 1.4,
                "apdex": 0.5,
                "unique_ips": 200,
            }
        ]
    )


def test_fast_endpoint_getting_less_fast_is_not_an_incident():
    """195 ms is not slow, however many sigma it is from a 60 ms baseline.

    This fired 1,635 times in a 7-hour replay before the gate existed.
    """
    history = _endpoint_history("/api/v1/user/profile", p95=60.0, error_rate=0.01)
    current = _endpoint_now("/api/v1/user/profile", p95=195.0, error_rate=0.01, requests=400)
    result = analyze_endpoints(current, history, THRESHOLDS)
    assert AnomalyType.SLOW_ENDPOINT not in {a["type"] for a in result}


def test_genuinely_slow_endpoint_is_still_detected():
    history = _endpoint_history("/api/v1/checkout", p95=380.0, error_rate=0.015)
    current = _endpoint_now("/api/v1/checkout", p95=3400.0, error_rate=0.015, requests=400)
    result = analyze_endpoints(current, history, THRESHOLDS)
    assert AnomalyType.SLOW_ENDPOINT in {a["type"] for a in result}


def test_error_rate_on_a_tiny_sample_is_not_claimed():
    """22 requests cannot support an error-rate claim: one failure moves it 5 points."""
    history = _endpoint_history("/api/v1/auth/login", p95=250.0, error_rate=0.07)
    current = _endpoint_now("/api/v1/auth/login", p95=250.0, error_rate=0.18, requests=22)
    result = analyze_endpoints(current, history, THRESHOLDS)
    assert result == []


def test_material_server_error_spike_is_detected():
    history = _endpoint_history("/api/v1/checkout", p95=300.0, error_rate=0.015, server_error_rate=0.003)
    current = _endpoint_now(
        "/api/v1/checkout", p95=300.0, error_rate=0.42, requests=600, server_error_rate=0.38
    )
    result = analyze_endpoints(current, history, THRESHOLDS)
    assert AnomalyType.ERROR_SPIKE in {a["type"] for a in result}


def test_client_errors_alone_are_not_a_service_error_spike():
    """401s from an auth-protected route are the service working correctly.

    Anonymous clients give /api/v1/cart a permanent double-digit 4xx rate.
    Treating that as a service fault made those endpoints alert continuously —
    140 false positives in a single 7-hour replay.
    """
    history = _endpoint_history("/api/v1/cart", p95=200.0, error_rate=0.10, server_error_rate=0.002)
    current = _endpoint_now(
        "/api/v1/cart", p95=200.0, error_rate=0.24, requests=600, server_error_rate=0.002
    )
    result = analyze_endpoints(current, history, THRESHOLDS)
    assert AnomalyType.ERROR_SPIKE not in {a["type"] for a in result}


# ── Geographic detection ─────────────────────────────────────────────────────
def _geo_rows(window, shares, total=40_000):
    """Build one window of per-country metrics from a share mapping."""
    return [
        {
            "window_start": window,
            "window_end": window + timedelta(minutes=5),
            "country": country,
            "country_name": country,
            "requests": int(total * share),
            "unique_ips": max(int(total * share / 40), 1),
            "error_rate": 0.02,
            "p95_response_time": 300.0,
        }
        for country, share in shares.items()
    ]


def test_established_origin_riding_the_daily_tide_is_not_flagged():
    """When platform traffic doubles, every country doubles with it.

    India, France and Australia were flagged every morning because their
    *absolute* volume rose. Their share does not move.
    """
    from loglens_common.analyzers import analyze_geo

    steady = {"US": 0.40, "IN": 0.12, "DE": 0.10, "GB": 0.08, "JP": 0.07, "FR": 0.07, "AU": 0.06, "BR": 0.10}
    history = []
    for i in range(30, 0, -1):
        # Total volume climbs 6x across the history; every share is constant.
        history += _geo_rows(START - timedelta(minutes=5 * i), steady, total=8_000 + 1_600 * (30 - i))
    current = _geo_rows(START, steady, total=56_000)

    found = analyze_geo(pd.DataFrame(current), pd.DataFrame(history), THRESHOLDS)
    assert found == [], f"steady shares must not alert, got {[a['entity'] for a in found]}"


def test_new_hostile_origin_at_a_few_percent_is_flagged():
    """A network going from ~0% to 3% of traffic is a huge relative change.

    Measured on real injected `geo_shift` scenarios, hostile origins reach only
    1-4% of platform traffic — an absolute 5-point floor suppressed every one.
    """
    from loglens_common.analyzers import analyze_geo

    steady = {"US": 0.43, "IN": 0.13, "DE": 0.11, "GB": 0.09, "JP": 0.08, "FR": 0.08, "AU": 0.08}
    history = []
    for i in range(30, 0, -1):
        history += _geo_rows(START - timedelta(minutes=5 * i), steady, total=40_000)

    shifted = dict(steady)
    shifted = {k: v * 0.96 for k, v in shifted.items()}
    shifted["BG"] = 0.04  # the hostile origin appears at 4%
    current = _geo_rows(START, shifted, total=40_000)

    found = analyze_geo(pd.DataFrame(current), pd.DataFrame(history), THRESHOLDS)
    assert "BG" in {a["entity"] for a in found}, f"got {[a['entity'] for a in found]}"
