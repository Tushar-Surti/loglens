"""Unit tests for the statistical detector library."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from loglens_common.detectors import (
    cusum_detector,
    ewma_detector,
    fuse,
    gini,
    make_anomaly,
    normalized_entropy,
    robust_stats,
    robust_zscore_detector,
    seasonal_detector,
    soft_score,
)
from loglens_common.schemas import AnomalyType, EntityType, Severity

THRESHOLDS = {
    "severity_low": 30,
    "severity_medium": 50,
    "severity_high": 70,
    "severity_critical": 85,
}


def test_robust_stats_ignores_single_outlier():
    baseline = [100.0] * 30
    contaminated = baseline + [100_000.0]
    clean = robust_stats(baseline)
    dirty = robust_stats(contaminated)
    # The median must barely move even though the mean explodes.
    assert dirty["median"] == pytest.approx(clean["median"], rel=0.01)
    assert dirty["mean"] > clean["mean"] * 10


def test_robust_zscore_flags_spike_not_noise():
    history = list(np.random.default_rng(0).normal(1000, 40, 60))
    normal = robust_zscore_detector(history, 1020, threshold=3.5, direction="up")
    spike = robust_zscore_detector(history, 3000, threshold=3.5, direction="up")
    assert not normal.triggered
    assert normal.score < 0.2
    assert spike.triggered
    assert spike.score > 0.9
    assert "rolling median" in spike.reason


def test_robust_zscore_direction_is_respected():
    history = [1000.0] * 40
    drop_up = robust_zscore_detector(history, 100, direction="up")
    drop_down = robust_zscore_detector(history, 100, direction="down")
    assert not drop_up.triggered
    assert drop_down.triggered


def test_cold_start_returns_neutral():
    result = robust_zscore_detector([1, 2, 3], 500, min_points=12)
    assert result.score == 0.0
    assert result.evidence["status"] == "cold_start"
    # Cold-start detectors must not dilute the fused score.
    assert fuse([result])["score"] == 0.0


def test_ewma_tracks_level_shift():
    history = [500.0] * 40
    result = ewma_detector(history, 2500.0, alpha=0.3, threshold=3.0)
    assert result.triggered
    assert result.evidence["forecast"] == pytest.approx(500.0, abs=1.0)


def test_cusum_detects_sustained_shift_that_zscore_normalises_away():
    #  A step change that persists: after several windows the new level is the
    #  new median, so the z-score fades — CUSUM keeps accumulating.
    history = [0.01] * 30 + [0.06] * 12
    change = cusum_detector(history, 0.06, decision_h=4.0)
    zscore = robust_zscore_detector(history, 0.06, threshold=3.5, direction="up")
    assert change.score > zscore.score
    assert change.evidence["direction"] == "increase"


def test_seasonal_detector_uses_time_of_day():
    now = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    rows = []
    for day in range(1, 5):
        for hour in range(24):
            rows.append(
                {
                    "window_start": now - timedelta(days=day) + timedelta(hours=hour - 10),
                    # Strong daily shape: busy at 10:00, quiet at 03:00.
                    "value": 1000 if hour == 10 else 200,
                }
            )
    history = pd.DataFrame(rows)

    typical = seasonal_detector(history, 1010, now, "value", threshold=3.0)
    unusual = seasonal_detector(history, 4000, now, "value", threshold=3.0)
    assert not typical.triggered
    assert unusual.triggered
    assert unusual.evidence["seasonal_baseline"] == pytest.approx(1000, abs=1)


def test_entropy_and_gini_describe_distribution_shape():
    even = [10] * 20
    concentrated = [1000] + [1] * 19
    assert normalized_entropy(even) == pytest.approx(1.0, abs=0.01)
    assert normalized_entropy(concentrated) < 0.35
    assert gini(even) == pytest.approx(0.0, abs=0.01)
    assert gini(concentrated) > 0.85


def test_soft_score_is_monotonic_and_centred():
    assert soft_score(3.5, 3.5) == pytest.approx(0.5, abs=1e-6)
    assert soft_score(7.0, 3.5) > 0.9
    assert soft_score(0.0, 3.5) < 0.1
    values = [soft_score(z, 3.5) for z in range(0, 10)]
    assert values == sorted(values)


def test_fuse_reinforces_agreement_without_diluting():
    class Result:
        def __init__(self, detector, score):
            self.detector, self.score = detector, score
            self.evidence = {}
            self.triggered = score > 0.5
            self.reason = ""

    single = fuse([Result("robust_zscore", 0.8)])
    agreeing = fuse([Result("robust_zscore", 0.8), Result("ewma", 0.75), Result("seasonal_baseline", 0.7)])
    diluted = fuse([Result("robust_zscore", 0.8), Result("ewma", 0.0)])

    assert agreeing["score"] > single["score"]
    assert diluted["score"] >= single["score"]
    assert agreeing["agreement"] == 3
    assert agreeing["confidence"] > single["confidence"]


def test_make_anomaly_is_idempotent_and_respects_the_floor():
    start = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=1)
    history = [1000.0] * 40
    strong = robust_zscore_detector(history, 9000, direction="up")

    first = make_anomaly(
        AnomalyType.TRAFFIC_SPIKE, EntityType.GLOBAL, "all-traffic", start, end,
        [strong], {"requests": 9000}, THRESHOLDS, "spike",
    )
    second = make_anomaly(
        AnomalyType.TRAFFIC_SPIKE, EntityType.GLOBAL, "all-traffic", start, end,
        [strong], {"requests": 9000}, THRESHOLDS, "spike",
    )
    assert first is not None
    assert first["anomaly_id"] == second["anomaly_id"]
    assert first["severity"] in (Severity.HIGH, Severity.CRITICAL)

    weak = robust_zscore_detector(history, 1010, direction="up")
    assert make_anomaly(
        AnomalyType.TRAFFIC_SPIKE, EntityType.GLOBAL, "all-traffic", start, end,
        [weak], {"requests": 1010}, THRESHOLDS, "spike",
    ) is None


def test_severity_ladder():
    assert Severity.from_score(95, THRESHOLDS) == Severity.CRITICAL
    assert Severity.from_score(72, THRESHOLDS) == Severity.HIGH
    assert Severity.from_score(55, THRESHOLDS) == Severity.MEDIUM
    assert Severity.from_score(35, THRESHOLDS) == Severity.LOW
    assert Severity.from_score(5, THRESHOLDS) == Severity.INFO


def test_trend_detector_ignores_a_smooth_ramp_but_catches_a_spike():
    """The morning ramp is not an incident; a departure from it is.

    A rolling median lags a rising series, so a plain z-score fires on every
    window of a diurnal climb. The trend detector forecasts along the slope.
    """
    from loglens_common.detectors import trend_residual_detector

    # A steady climb of 100 requests per window, with mild noise.
    rng = np.random.default_rng(3)
    ramp = [5000 + 100 * i + rng.normal(0, 40) for i in range(60)]
    next_on_trend = 5000 + 100 * 60

    on_trend = trend_residual_detector(ramp, next_on_trend, threshold=3.5)
    spike = trend_residual_detector(ramp, next_on_trend * 3, threshold=3.5)
    plain_z = robust_zscore_detector(ramp, next_on_trend, threshold=3.5, direction="up")

    assert not on_trend.triggered, "a window continuing the trend must not fire"
    assert on_trend.score < 0.2
    assert spike.triggered
    assert spike.score > 0.9
    # The whole point: the plain z-score is fooled by the ramp.
    assert plain_z.score > on_trend.score
    assert on_trend.evidence["slope_per_window"] == pytest.approx(100, abs=15)


def test_trend_detector_is_robust_to_a_contaminated_history():
    """One prior spike must not tilt the forecast (Theil-Sen, not least squares)."""
    from loglens_common.detectors import trend_residual_detector

    flat = [1000.0] * 40
    contaminated = flat[:20] + [90_000.0] + flat[21:]
    result = trend_residual_detector(contaminated, 1000.0, threshold=3.5)
    assert abs(result.evidence["slope_per_window"]) < 5
    assert not result.triggered
