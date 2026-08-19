"""Anomaly-detection library.

Pure NumPy/pandas so the identical code runs inside Spark ``foreachBatch``
callbacks, in the offline evaluation harness, and in unit tests.

Design notes
------------
* **No single detector decides anything.**  Each detector returns a normalised
  ``[0, 1]`` score plus its own evidence; :func:`fuse` combines the active
  detectors into a final ``0-100`` anomaly score, and severity is derived from
  that.  This is what separates the system from "if value > X then alert".
* **Robust statistics everywhere.**  Median/MAD instead of mean/std, so a single
  extreme window does not poison the baseline it is being compared against.
* **Seasonality is modelled.**  Web traffic has a strong daily shape; comparing
  10:00 against the last 30 minutes flags every morning ramp.  The seasonal
  detector compares a window against the same time-of-day bucket on previous
  days.
* **Cold start is handled.**  Detectors needing history return a neutral score
  until ``min_history_points`` samples exist; the multivariate detector falls
  back to a Mahalanobis distance when no trained model is available yet.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from .schemas import AnomalyType, Detector, Severity

EPS = 1e-9


# ── Result container ─────────────────────────────────────────────────────────
@dataclass
class DetectionResult:
    """Normalised output of one detector."""

    detector: str
    score: float  # 0..1
    triggered: bool
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def neutral(cls, detector: str, reason: str = "insufficient history") -> "DetectionResult":
        return cls(detector=detector, score=0.0, triggered=False, reason=reason, evidence={"status": "cold_start"})


# ── Primitive statistics ─────────────────────────────────────────────────────
def robust_stats(values: Sequence[float]) -> Dict[str, float]:
    """Median and MAD-derived sigma (1.4826 * MAD ≈ std for gaussian data)."""
    arr = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if arr.size == 0:
        return {"median": 0.0, "mad": 0.0, "sigma": 0.0, "n": 0, "mean": 0.0, "std": 0.0}
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    sigma = 1.4826 * mad
    if sigma < EPS:
        # Degenerate MAD (many identical values): fall back to std, then to a
        # small fraction of the level so the z-score stays finite.
        sigma = float(np.std(arr))
    if sigma < EPS:
        sigma = max(abs(median) * 0.10, 1.0)
    return {
        "median": median,
        "mad": mad,
        "sigma": sigma,
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
    }


def soft_score(z: float, threshold: float, sharpness: float = 3.0) -> float:
    """Map a z-score to ``[0, 1]`` with a logistic curve centred on ``threshold``.

    ``z == threshold`` → 0.5, ``z == 2*threshold`` → ~0.95.  Smooth scores are
    what make ranking and score-fusion meaningful; a step function would not.
    """
    if not np.isfinite(z):
        return 0.0
    width = max(threshold / sharpness, EPS)
    return float(1.0 / (1.0 + math.exp(-(z - threshold) / width)))


def ratio_score(observed: float, expected: float, multiplier: float) -> float:
    """Score for "observed is ``multiplier`` times its expected value"."""
    if expected <= EPS:
        return 1.0 if observed > EPS else 0.0
    ratio = observed / expected
    return soft_score(ratio, multiplier, sharpness=4.0)


def shannon_entropy(counts: Iterable[float]) -> float:
    """Entropy in bits — used to describe how spread a distribution is."""
    arr = np.asarray([c for c in counts if c > 0], dtype=float)
    if arr.size <= 1:
        return 0.0
    p = arr / arr.sum()
    return float(-np.sum(p * np.log2(p)))


def normalized_entropy(counts: Iterable[float]) -> float:
    arr = np.asarray([c for c in counts if c > 0], dtype=float)
    if arr.size <= 1:
        return 0.0
    return float(shannon_entropy(arr) / math.log2(arr.size))


def gini(values: Iterable[float]) -> float:
    """Concentration of traffic across sources.  1.0 = a single IP owns it all."""
    arr = np.sort(np.asarray([v for v in values if v >= 0], dtype=float))
    n = arr.size
    if n == 0 or arr.sum() <= EPS:
        return 0.0
    index = np.arange(1, n + 1)
    return float((np.sum((2 * index - n - 1) * arr)) / (n * arr.sum()))


# ── Univariate detectors ─────────────────────────────────────────────────────
def robust_zscore_detector(
    history: Sequence[float],
    value: float,
    threshold: float = 3.5,
    min_points: int = 12,
    direction: str = "both",
    label: str = "value",
) -> DetectionResult:
    """Median/MAD outlier test — the workhorse for spikes and drops."""
    stats = robust_stats(history)
    if stats["n"] < min_points:
        return DetectionResult.neutral(Detector.ROBUST_Z)

    z = (value - stats["median"]) / max(stats["sigma"], EPS)
    if direction == "up":
        effective = max(z, 0.0)
    elif direction == "down":
        effective = max(-z, 0.0)
    else:
        effective = abs(z)

    score = soft_score(effective, threshold)
    delta_pct = ((value - stats["median"]) / max(abs(stats["median"]), EPS)) * 100.0
    reason = (
        f"{label} is {value:,.2f} against a rolling median of {stats['median']:,.2f} "
        f"({delta_pct:+.0f}%, {effective:.1f}σ over {stats['n']} windows)"
    )
    return DetectionResult(
        detector=Detector.ROBUST_Z,
        score=score,
        triggered=bool(effective >= threshold),
        reason=reason,
        evidence={
            "observed": round(float(value), 4),
            "baseline_median": round(stats["median"], 4),
            "sigma": round(stats["sigma"], 4),
            "z_score": round(float(effective), 3),
            "delta_pct": round(float(delta_pct), 2),
            "history_points": stats["n"],
        },
    )


def ewma_detector(
    history: Sequence[float],
    value: float,
    alpha: float = 0.25,
    threshold: float = 3.5,
    min_points: int = 12,
    label: str = "value",
) -> DetectionResult:
    """Exponentially-weighted control chart.

    Tracks the level with an EWMA and the dispersion with an EW mean-absolute
    deviation, then measures how far the new sample sits from the forecast.
    Reacts faster than a plain rolling median to sustained shifts.
    """
    arr = np.asarray([v for v in history if v is not None and np.isfinite(v)], dtype=float)
    if arr.size < min_points:
        return DetectionResult.neutral(Detector.EWMA)

    level = float(arr[0])
    deviation = 0.0
    for sample in arr[1:]:
        residual = abs(sample - level)
        deviation = alpha * residual + (1 - alpha) * deviation
        level = alpha * sample + (1 - alpha) * level

    # EW-MAD → sigma conversion for a gaussian: sigma ≈ 1.2533 * MAD
    sigma = max(1.2533 * deviation, max(abs(level) * 0.05, EPS))
    z = (value - level) / sigma
    score = soft_score(abs(z), threshold)
    return DetectionResult(
        detector=Detector.EWMA,
        score=score,
        triggered=bool(abs(z) >= threshold),
        reason=(
            f"{label} deviates {z:+.1f}σ from the EWMA forecast "
            f"({level:,.2f}, α={alpha})"
        ),
        evidence={
            "observed": round(float(value), 4),
            "forecast": round(level, 4),
            "sigma": round(float(sigma), 4),
            "z_score": round(float(z), 3),
            "alpha": alpha,
        },
    )


def seasonal_detector(
    history: pd.DataFrame,
    value: float,
    when: datetime,
    value_col: str = "value",
    time_col: str = "window_start",
    bucket_minutes: int = 15,
    threshold: float = 3.5,
    min_samples: int = 3,
    label: str = "value",
) -> DetectionResult:
    """Compare against the same time-of-day bucket on previous days.

    Without this, every morning traffic ramp looks like an incident.  Weekday
    and weekend profiles are kept separate because their shapes differ.
    """
    if history is None or history.empty or time_col not in history or value_col not in history:
        return DetectionResult.neutral(Detector.SEASONAL)

    frame = history.dropna(subset=[time_col, value_col]).copy()
    if frame.empty:
        return DetectionResult.neutral(Detector.SEASONAL)

    times = pd.to_datetime(frame[time_col], utc=True)
    minute_of_day = times.dt.hour * 60 + times.dt.minute
    bucket = (minute_of_day // bucket_minutes).astype(int)
    is_weekend = times.dt.dayofweek >= 5

    when_utc = pd.Timestamp(when).tz_convert("UTC") if pd.Timestamp(when).tzinfo else pd.Timestamp(when, tz="UTC")
    target_bucket = int((when_utc.hour * 60 + when_utc.minute) // bucket_minutes)
    target_weekend = when_utc.dayofweek >= 5

    # Same bucket, same day-type, excluding the last hour (that is "now").
    mask = (bucket == target_bucket) & (is_weekend == target_weekend) & (times < when_utc - pd.Timedelta(minutes=60))
    samples = frame.loc[mask.values, value_col].astype(float)
    if samples.size < min_samples:
        # Widen to neighbouring buckets before giving up.
        mask = (bucket.isin([target_bucket - 1, target_bucket, target_bucket + 1])) & (
            times < when_utc - pd.Timedelta(minutes=60)
        )
        samples = frame.loc[mask.values, value_col].astype(float)
    if samples.size < min_samples:
        return DetectionResult.neutral(Detector.SEASONAL)

    stats = robust_stats(samples.tolist())
    z = (value - stats["median"]) / max(stats["sigma"], EPS)
    score = soft_score(abs(z), threshold)
    day_type = "weekend" if target_weekend else "weekday"
    return DetectionResult(
        detector=Detector.SEASONAL,
        score=score,
        triggered=bool(abs(z) >= threshold),
        reason=(
            f"{label} is {value:,.2f}; the {day_type} baseline for "
            f"{when_utc:%H:%M} UTC is {stats['median']:,.2f} ({z:+.1f}σ, n={stats['n']})"
        ),
        evidence={
            "observed": round(float(value), 4),
            "seasonal_baseline": round(stats["median"], 4),
            "sigma": round(stats["sigma"], 4),
            "z_score": round(float(z), 3),
            "samples": stats["n"],
            "bucket_minutes": bucket_minutes,
            "day_type": day_type,
        },
    )


def trend_residual_detector(
    history: Sequence[float],
    value: float,
    threshold: float = 3.5,
    min_points: int = 12,
    lookback: int = 40,
    label: str = "value",
) -> DetectionResult:
    """Outlier test on the residual from a robust local trend.

    A rolling median lags a rising series, so during the morning ramp *every*
    window sits above its own baseline and a plain z-score fires continuously.
    Comparing against a trend-aware forecast removes that: a smooth ramp has
    small residuals however steep it is, while a genuine spike departs from the
    trend line.

    The slope is Theil-Sen (median of pairwise slopes) rather than least
    squares, so a single anomalous window in the history cannot drag the
    forecast toward itself.

    This is what lets volume detection work sensibly before enough days exist
    for the seasonal model.
    """
    arr = np.asarray([v for v in history if v is not None and np.isfinite(v)], dtype=float)
    if arr.size < min_points:
        return DetectionResult.neutral(Detector.TREND)

    window = arr[-lookback:]
    n = window.size
    index = np.arange(n, dtype=float)

    # Theil-Sen slope over pairwise differences.
    rows, cols = np.triu_indices(n, k=1)
    deltas = index[cols] - index[rows]
    slopes = (window[cols] - window[rows]) / np.where(deltas == 0, 1.0, deltas)
    slope = float(np.median(slopes)) if slopes.size else 0.0
    intercept = float(np.median(window - slope * index))

    fitted = intercept + slope * index
    residuals = window - fitted
    spread = 1.4826 * float(np.median(np.abs(residuals - np.median(residuals))))
    if spread < EPS:
        spread = max(float(np.std(residuals)), max(abs(intercept) * 0.02, 1.0))

    predicted = intercept + slope * n
    residual = value - predicted
    z = residual / max(spread, EPS)
    score = soft_score(max(z, 0.0), threshold)

    return DetectionResult(
        detector=Detector.TREND,
        score=score,
        triggered=bool(z >= threshold),
        reason=(
            f"{label} is {value:,.2f} against a trend-adjusted forecast of {predicted:,.2f} "
            f"({z:+.1f}σ from the local trend of {slope:+,.1f} per window)"
        ),
        evidence={
            "observed": round(float(value), 4),
            "trend_forecast": round(float(predicted), 4),
            "slope_per_window": round(slope, 4),
            "residual": round(float(residual), 4),
            "residual_sigma": round(float(spread), 4),
            "z_score": round(float(z), 3),
            "lookback_windows": int(n),
        },
    )


def cusum_detector(
    history: Sequence[float],
    value: float,
    drift_k: float = 0.5,
    decision_h: float = 5.0,
    min_points: int = 12,
    label: str = "value",
) -> DetectionResult:
    """Two-sided CUSUM change-point test.

    Detects *sustained* regime shifts (e.g. an error rate that steps from 1% to
    6% and stays there) which spike detectors under-weight after a couple of
    windows because the shift becomes the new normal.
    """
    arr = np.asarray([v for v in history if v is not None and np.isfinite(v)], dtype=float)
    if arr.size < min_points:
        return DetectionResult.neutral(Detector.CUSUM)

    stats = robust_stats(arr.tolist())
    sigma = max(stats["sigma"], EPS)
    standardized = np.append((arr - stats["median"]) / sigma, (value - stats["median"]) / sigma)

    s_pos = s_neg = 0.0
    peak_pos = peak_neg = 0.0
    for x in standardized:
        s_pos = max(0.0, s_pos + x - drift_k)
        s_neg = max(0.0, s_neg - x - drift_k)
        peak_pos, peak_neg = max(peak_pos, s_pos), max(peak_neg, s_neg)

    magnitude = max(s_pos, s_neg)
    score = soft_score(magnitude, decision_h, sharpness=3.0)
    direction = "increase" if s_pos >= s_neg else "decrease"
    return DetectionResult(
        detector=Detector.CUSUM,
        score=score,
        triggered=bool(magnitude >= decision_h),
        reason=(
            f"CUSUM detects a sustained {direction} in {label} "
            f"(statistic {magnitude:.1f} vs decision limit {decision_h:.1f})"
        ),
        evidence={
            "cusum_pos": round(float(s_pos), 3),
            "cusum_neg": round(float(s_neg), 3),
            "decision_limit": decision_h,
            "direction": direction,
            "baseline_median": round(stats["median"], 4),
        },
    )


# ── Multivariate detectors ───────────────────────────────────────────────────
def mahalanobis_detector(
    history: pd.DataFrame,
    row: pd.Series,
    features: Sequence[str],
    threshold: float = 3.5,
    min_points: int = 20,
) -> DetectionResult:
    """Covariance-aware distance — the cold-start stand-in for IsolationForest.

    Catches combinations that are individually unremarkable but jointly odd
    (e.g. normal request volume with an abnormal unique-path/error mix).
    """
    if history is None or history.empty:
        return DetectionResult.neutral(Detector.MAHALANOBIS)
    available = [f for f in features if f in history.columns and f in row.index]
    if len(available) < 2:
        return DetectionResult.neutral(Detector.MAHALANOBIS)

    matrix = history[available].astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if matrix.shape[0] < min_points:
        return DetectionResult.neutral(Detector.MAHALANOBIS)

    center = matrix.median().values
    cov = np.cov(matrix.values, rowvar=False)
    cov = np.atleast_2d(cov) + np.eye(len(available)) * 1e-6
    try:
        inv_cov = np.linalg.pinv(cov)
    except np.linalg.LinAlgError:  # pragma: no cover - defensive
        return DetectionResult.neutral(Detector.MAHALANOBIS)

    delta = row[available].astype(float).values - center
    distance = float(np.sqrt(max(delta @ inv_cov @ delta.T, 0.0)))
    score = soft_score(distance, threshold, sharpness=2.5)
    contributions = {
        feature: round(float(abs(delta[i]) / max(np.sqrt(cov[i][i]), EPS)), 2)
        for i, feature in enumerate(available)
    }
    top = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return DetectionResult(
        detector=Detector.MAHALANOBIS,
        score=score,
        triggered=bool(distance >= threshold),
        reason=(
            "multivariate profile is unusual (Mahalanobis "
            f"{distance:.1f}); largest deviations: "
            + ", ".join(f"{name} {value:.1f}σ" for name, value in top)
        ),
        evidence={
            "method": "mahalanobis_fallback",
            "distance": round(distance, 3),
            "features": available,
            "per_feature_sigma": contributions,
        },
    )


class ModelBundle:
    """A trained IsolationForest + scaler + feature list loaded from disk.

    Loading is best-effort: if scikit-learn or the artefact is unavailable the
    bundle reports ``ready == False`` and callers transparently fall back to the
    statistical detectors.
    """

    def __init__(self, name: str, model=None, scaler=None, features: Optional[Sequence[str]] = None,
                 metadata: Optional[Dict[str, Any]] = None):
        self.name = name
        self.model = model
        self.scaler = scaler
        self.features: List[str] = list(features or [])
        self.metadata: Dict[str, Any] = metadata or {}

    @property
    def ready(self) -> bool:
        return self.model is not None and bool(self.features)

    @classmethod
    def load(cls, path: str, name: str = "model") -> "ModelBundle":
        try:
            import joblib

            payload = joblib.load(path)
            return cls(
                name=name,
                model=payload.get("model"),
                scaler=payload.get("scaler"),
                features=payload.get("features", []),
                metadata=payload.get("metadata", {}),
            )
        except Exception:  # pragma: no cover - absence is an expected state
            return cls(name=name)

    def score_frame(self, frame: pd.DataFrame) -> np.ndarray:
        """Return per-row anomaly scores in ``[0, 1]`` (higher = more anomalous)."""
        if not self.ready or frame.empty:
            return np.zeros(len(frame))
        missing = [f for f in self.features if f not in frame.columns]
        if missing:
            for column in missing:
                frame = frame.assign(**{column: 0.0})
        matrix = frame[self.features].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).values
        if self.scaler is not None:
            matrix = self.scaler.transform(matrix)
        # decision_function: positive = inlier, negative = outlier.
        raw = self.model.decision_function(matrix)
        # Map to 0..1 with a logistic centred on the trained offset.
        return np.clip(1.0 / (1.0 + np.exp(raw * 6.0)), 0.0, 1.0)


def isolation_forest_detector(
    bundle: Optional[ModelBundle],
    frame: pd.DataFrame,
    row_index: int,
    threshold: float = 0.6,
) -> DetectionResult:
    if bundle is None or not bundle.ready:
        return DetectionResult.neutral(Detector.ISOLATION_FOREST, "model not trained yet")
    scores = bundle.score_frame(frame)
    score = float(scores[row_index]) if row_index < len(scores) else 0.0
    return DetectionResult(
        detector=Detector.ISOLATION_FOREST,
        score=score,
        triggered=bool(score >= threshold),
        reason=f"IsolationForest ({bundle.metadata.get('trees', 'n/a')} trees) scores this profile {score:.2f}",
        evidence={
            "method": "isolation_forest",
            "model_score": round(score, 3),
            "features": bundle.features,
            "trained_at": bundle.metadata.get("trained_at"),
            "training_rows": bundle.metadata.get("rows"),
        },
    )


# ── Fusion ───────────────────────────────────────────────────────────────────
DEFAULT_WEIGHTS: Dict[str, float] = {
    Detector.ROBUST_Z: 1.0,
    Detector.EWMA: 0.9,
    Detector.SEASONAL: 1.2,
    Detector.CUSUM: 0.8,
    Detector.TREND: 1.15,
    Detector.ISOLATION_FOREST: 1.1,
    Detector.MAHALANOBIS: 0.7,
    Detector.ENTROPY: 1.0,
    Detector.RULE: 1.3,
}


def fuse(results: Sequence[DetectionResult], weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Combine detector outputs into a single 0-100 score.

    Uses a weighted soft-OR (noisy-OR) rather than an average: independent
    detectors agreeing should reinforce, but one silent detector must not dilute
    a confident one.  Cold-start (neutral) detectors are excluded entirely.
    """
    weights = weights or DEFAULT_WEIGHTS
    active = [r for r in results if r.evidence.get("status") != "cold_start"]
    if not active:
        return {"score": 0.0, "confidence": 0.0, "detectors": [], "agreement": 0}

    complement = 1.0
    weight_sum = 0.0
    for result in active:
        weight = weights.get(result.detector, 1.0)
        adjusted = min(max(result.score * min(weight, 1.5), 0.0), 0.999)
        complement *= (1.0 - adjusted)
        weight_sum += weight
    combined = 1.0 - complement

    agreement = sum(1 for r in active if r.score >= 0.5)
    # Confidence rises with the number of independent detectors that had enough
    # data to speak, saturating at four.
    confidence = min(1.0, len(active) / 4.0) * (0.6 + 0.4 * min(agreement, 3) / 3.0)

    return {
        "score": round(float(combined * 100.0), 2),
        "confidence": round(float(confidence), 3),
        "agreement": agreement,
        "detectors": [r.detector for r in active if r.score >= 0.3],
        "weight_sum": round(weight_sum, 2),
    }


def build_reason(primary: str, results: Sequence[DetectionResult], limit: int = 3) -> str:
    """Human-readable explanation: the headline plus the strongest evidence."""
    contributors = sorted(
        (r for r in results if r.score >= 0.25 and r.evidence.get("status") != "cold_start"),
        key=lambda r: r.score,
        reverse=True,
    )[:limit]
    if not contributors:
        return primary
    detail = "; ".join(r.reason for r in contributors if r.reason)
    return f"{primary}. {detail}" if detail else primary


def anomaly_id(anomaly_type: str, entity_type: str, entity: str, window_start: datetime) -> str:
    """Deterministic id → re-processing a window updates instead of duplicating.

    Structured Streaming re-emits windows in ``update`` mode and Kafka gives us
    at-least-once delivery, so the sink must be idempotent.
    """
    digest = hashlib.sha1(
        f"{anomaly_type}|{entity_type}|{entity}|{window_start.isoformat()}".encode("utf-8")
    ).hexdigest()
    return f"anm_{digest[:20]}"


def make_anomaly(
    anomaly_type: str,
    entity_type: str,
    entity: str,
    window_start: datetime,
    window_end: datetime,
    results: Sequence[DetectionResult],
    metrics: Dict[str, Any],
    thresholds: Dict[str, float],
    headline: str,
    ground_truth: Optional[str] = None,
    service: Optional[str] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Assemble a persisted anomaly document, or ``None`` if below noise floor."""
    fused = fuse(results, weights)
    score = fused["score"]
    minimum = float(thresholds.get("severity_low", 30))
    if score < minimum:
        return None

    severity = Severity.from_score(score, thresholds)
    contributing = [
        {
            "detector": r.detector,
            "score": round(r.score, 3),
            "triggered": r.triggered,
            "reason": r.reason,
            "evidence": r.evidence,
        }
        for r in results
        if r.evidence.get("status") != "cold_start"
    ]
    return {
        "anomaly_id": anomaly_id(anomaly_type, entity_type, entity, window_start),
        "type": anomaly_type,
        "type_label": AnomalyType.LABELS.get(anomaly_type, anomaly_type),
        "entity_type": entity_type,
        "entity": entity,
        "service": service,
        "score": score,
        "confidence": fused["confidence"],
        "severity": severity,
        "detector": max(results, key=lambda r: r.score).detector if results else Detector.RULE,
        "detectors": fused["detectors"],
        "agreement": fused["agreement"],
        "reason": build_reason(headline, results),
        "headline": headline,
        "window_start": window_start,
        "window_end": window_end,
        "detected_at": datetime.now(timezone.utc),
        "metrics": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in metrics.items()},
        "contributing": contributing,
        "evidence": {
            r.detector: r.evidence for r in results if r.evidence.get("status") != "cold_start"
        },
        "status": "open",
        "incident_id": None,
        "ground_truth": ground_truth,
    }


__all__ = [
    "DetectionResult",
    "ModelBundle",
    "anomaly_id",
    "build_reason",
    "cusum_detector",
    "ewma_detector",
    "fuse",
    "gini",
    "isolation_forest_detector",
    "mahalanobis_detector",
    "make_anomaly",
    "normalized_entropy",
    "ratio_score",
    "robust_stats",
    "robust_zscore_detector",
    "trend_residual_detector",
    "seasonal_detector",
    "shannon_entropy",
    "soft_score",
]
