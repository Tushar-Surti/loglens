"""Domain analyzers: turn windowed metrics into explained anomalies.

Each ``analyze_*`` function receives the metrics produced by Spark for the
current micro-batch plus a rolling history read back from MongoDB, runs the
relevant detector ensemble from :mod:`loglens_common.detectors`, and returns
ready-to-persist anomaly documents.

The split matters: Spark owns *distributed aggregation*, these functions own
*statistics and interpretation*, and they stay pure so they can be unit-tested
and replayed offline against labelled data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .detectors import (
    DetectionResult,
    ModelBundle,
    cusum_detector,
    ewma_detector,
    isolation_forest_detector,
    mahalanobis_detector,
    make_anomaly,
    robust_zscore_detector,
    seasonal_detector,
    trend_residual_detector,
    soft_score,
)
from .schemas import AnomalyType, Detector, EntityType

# ── Materiality gates ────────────────────────────────────────────────────────
# Statistical significance is not the same as operational significance.
#
# An endpoint whose p95 normally sits at 60 ms and briefly reaches 195 ms is a
# 12σ event and completely irrelevant — nobody is paged because a fast endpoint
# got slightly less fast. Likewise an error rate computed over 22 requests
# cannot support a claim about anything: one extra failure moves it five points.
#
# These gates are preconditions applied *before* scoring, not fudge factors
# applied after. A detection must be both statistically unusual AND materially
# different for anyone to care about it.
ENDPOINT_MIN_REQUESTS = 40          # a p95 is estimable from ~40 samples; rates need far more
RATE_MIN_SAMPLE = 100               # minimum denominator for an error-rate claim
# Error detection keys on *server* errors.
#
# A 401 from an auth-protected route is the service working correctly: anonymous
# clients hitting /api/v1/cart produce a permanent double-digit 4xx rate that has
# nothing to do with service health. Weighting 4xx equally with 5xx made those
# endpoints alert continuously. 5xx is the signal that something is broken.
SERVER_ERROR_FLOOR = 0.02           # 2% of requests failing server-side is material
SERVER_ERROR_DELTA = 0.015          # ...and must be 1.5 points above its own baseline
ERROR_MATERIAL_FLOOR = 0.05         # total-error floor, used only as a secondary check
ERROR_MATERIAL_DELTA = 0.03
LATENCY_MATERIAL_MS = 400.0         # a p95 under 400 ms is not "slow" on its own...
LATENCY_MATERIAL_MULTIPLE = 1.6     # ...and must also be this multiple of its baseline.
# Second tier: a severe *relative* degradation counts even at a lower absolute
# number, because an endpoint that normally answers in 60 ms and now takes
# 300 ms is genuinely broken — it just never crosses a flat 400 ms floor.
LATENCY_SEVERE_MS = 250.0
LATENCY_SEVERE_MULTIPLE = 3.0


def _latency_is_material(p95: float, baseline: float) -> bool:
    """Either a slow absolute response, or a severe relative degradation."""
    if baseline <= 0:
        return p95 >= LATENCY_MATERIAL_MS
    return (p95 >= LATENCY_MATERIAL_MS and p95 >= baseline * LATENCY_MATERIAL_MULTIPLE) or (
        p95 >= LATENCY_SEVERE_MS and p95 >= baseline * LATENCY_SEVERE_MULTIPLE
    )
#: Without previous days to compare against, a diurnal ramp is indistinguishable
#: from a spike, so volume detection demands a larger deviation until the
#: seasonal detector has data.
COLD_START_Z_PENALTY = 1.8

GLOBAL_FEATURES = [
    "rps",
    "error_rate",
    "server_error_rate",
    "p95_response_time",
    "unique_ips",
    "bytes_per_request",
    "ip_gini",
    "path_entropy",
    "bot_ratio",
]

IP_FEATURES = [
    "requests",
    "unique_paths",
    "unique_user_agents",
    "error_ratio",
    "not_found_ratio",
    "auth_fail_count",
    "avg_response_time",
    "bytes_per_request",
    "path_entropy",
    "burstiness",
]


def _rule(score: float, reason: str, evidence: Dict[str, Any], triggered: Optional[bool] = None) -> DetectionResult:
    """Wrap a domain rule as a detector so it participates in score fusion."""
    score = float(min(max(score, 0.0), 1.0))
    return DetectionResult(
        detector=Detector.RULE,
        score=score,
        triggered=score >= 0.5 if triggered is None else triggered,
        reason=reason,
        evidence=evidence,
    )


def _series(history: pd.DataFrame, column: str) -> List[float]:
    if history is None or history.empty or column not in history.columns:
        return []
    return history[column].astype(float).replace([np.inf, -np.inf], np.nan).dropna().tolist()


def _f(row: Any, key: str, default: float = 0.0) -> float:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _window(row: Any) -> tuple:
    start = pd.Timestamp(row["window_start"])
    if pd.isna(start):
        start = pd.Timestamp(datetime.now(timezone.utc)).floor("min")
    end = pd.Timestamp(row.get("window_end") or start + pd.Timedelta(minutes=1))
    if pd.isna(end):
        end = start + pd.Timedelta(minutes=1)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    return start.to_pydatetime(), end.to_pydatetime()


# ── Global traffic ───────────────────────────────────────────────────────────
def analyze_global(
    current: pd.Series,
    history: pd.DataFrame,
    thresholds: Dict[str, float],
    model: Optional[ModelBundle] = None,
) -> List[Dict[str, Any]]:
    """Platform-wide detections for a single completed metric window."""
    anomalies: List[Dict[str, Any]] = []
    z_threshold = float(thresholds.get("zscore_threshold", 3.5))
    min_points = int(thresholds.get("min_history_points", 12))
    alpha = float(thresholds.get("ewma_alpha", 0.25))
    start, end = _window(current)

    requests = _f(current, "requests")
    rps = _f(current, "rps", requests / 60.0)
    error_rate = _f(current, "error_rate")
    server_error_rate = _f(current, "server_error_rate")
    p95 = _f(current, "p95_response_time")
    ip_gini = _f(current, "ip_gini")
    path_entropy = _f(current, "path_entropy")
    unique_ips = _f(current, "unique_ips")
    ground_truth = current.get("dominant_attack_label") if hasattr(current, "get") else None

    base_metrics = {
        "requests": requests,
        "rps": rps,
        "error_rate": error_rate,
        "server_error_rate": server_error_rate,
        "p95_response_time": p95,
        "p99_response_time": _f(current, "p99_response_time"),
        "unique_ips": unique_ips,
        "unique_sessions": _f(current, "unique_sessions"),
        "ip_gini": ip_gini,
        "path_entropy": path_entropy,
    }

    # ── Traffic volume: spike and drop ───────────────────────────────────────
    volume_history = _series(history, "requests")
    seasonal = seasonal_detector(
        history, requests, start, "requests", "window_start", 15, z_threshold, label="request volume"
    )
    # With no previous days to compare against, the morning ramp and a real
    # spike look identical to a rolling baseline. Rather than emit confident
    # nonsense, demand a bigger deviation until seasonality is learnable.
    seasonal_blind = seasonal.evidence.get("status") == "cold_start"
    volume_z = z_threshold * (COLD_START_Z_PENALTY if seasonal_blind else 1.0)

    z_up = robust_zscore_detector(volume_history, requests, volume_z, min_points, "up", "request volume")
    ew = ewma_detector(volume_history, requests, alpha, volume_z, min_points, "request volume")
    change = cusum_detector(volume_history, requests, 0.5, 5.0 * (1.6 if seasonal_blind else 1.0), min_points, "request volume")
    trend = trend_residual_detector(volume_history, requests, z_threshold, min_points, label="request volume")

    spike = make_anomaly(
        AnomalyType.TRAFFIC_SPIKE,
        EntityType.GLOBAL,
        "all-traffic",
        start,
        end,
        [z_up, ew, seasonal, change, trend],
        base_metrics,
        thresholds,
        f"Request volume spike: {requests:,.0f} requests ({rps:,.1f} rps) in this window",
        ground_truth=ground_truth,
    )
    # Without a seasonal baseline, only the trend-aware detector can tell a
    # spike from the daily ramp — so it has to be the one that speaks.
    volume_confirmed = (not seasonal_blind) or trend.score >= 0.5
    if spike and requests > 0 and volume_confirmed:
        anomalies.append(spike)

    if not spike:
        z_down = robust_zscore_detector(volume_history, requests, volume_z, min_points, "down", "request volume")
        drop = make_anomaly(
            AnomalyType.TRAFFIC_DROP,
            EntityType.GLOBAL,
            "all-traffic",
            start,
            end,
            [z_down, seasonal, trend],
            base_metrics,
            thresholds,
            f"Traffic dropped to {requests:,.0f} requests — possible outage or upstream failure",
            ground_truth=ground_truth,
        )
        if drop:
            anomalies.append(drop)

    # ── DDoS-like burst: volume + source concentration + narrow path mix ─────
    if requests > 0 and volume_history:
        baseline = float(np.median(volume_history)) if volume_history else requests
        burst_ratio = requests / max(baseline, 1.0)
        concentration = soft_score(ip_gini, 0.72, sharpness=6.0)
        narrowness = soft_score(1.0 - path_entropy, 0.55, sharpness=5.0)
        volume_component = soft_score(burst_ratio, 2.2, sharpness=4.0)
        ddos_rule = _rule(
            score=float(np.cbrt(max(volume_component, 1e-6) * max(concentration, 1e-6) * max(narrowness, 1e-6))),
            reason=(
                f"volume is {burst_ratio:.1f}x baseline while {ip_gini:.2f} Gini source concentration "
                f"and {path_entropy:.2f} normalised path entropy indicate a narrow, machine-driven pattern"
            ),
            evidence={
                "burst_ratio": round(burst_ratio, 2),
                "ip_gini": round(ip_gini, 3),
                "path_entropy": round(path_entropy, 3),
                "unique_ips": unique_ips,
                "requests_per_ip": round(requests / max(unique_ips, 1), 2),
            },
        )
        if ddos_rule.score >= 0.35:
            ddos = make_anomaly(
                AnomalyType.DDOS_BURST,
                EntityType.GLOBAL,
                "all-traffic",
                start,
                end,
                [ddos_rule, z_up, ew],
                base_metrics,
                thresholds,
                f"DDoS-like burst: {rps:,.0f} rps concentrated in {unique_ips:,.0f} source IPs",
                ground_truth=ground_truth,
            )
            if ddos:
                anomalies.append(ddos)

    # ── Error rate ───────────────────────────────────────────────────────────
    error_history = _series(history, "error_rate")
    err_z = robust_zscore_detector(error_history, error_rate, z_threshold, min_points, "up", "error rate")
    err_cusum = cusum_detector(error_history, error_rate, 0.5, 4.0, min_points, "error rate")
    err_seasonal = seasonal_detector(history, error_rate, start, "error_rate", "window_start", 15, z_threshold, label="error rate")
    err_limit = float(thresholds.get("error_rate_threshold", 0.08))
    err_rule = _rule(
        score=soft_score(error_rate, err_limit, sharpness=2.5),
        reason=(
            f"{error_rate * 100:.1f}% of requests failed ({server_error_rate * 100:.1f}% server errors) "
            f"against a {err_limit * 100:.0f}% budget"
        ),
        evidence={
            "error_rate": round(error_rate, 4),
            "server_error_rate": round(server_error_rate, 4),
            "budget": err_limit,
            "failed_requests": int(_f(current, "errors_4xx") + _f(current, "errors_5xx")),
        },
    )
    server_history = _series(history, "server_error_rate")
    server_baseline = float(np.median(server_history)) if server_history else 0.0
    global_error_material = server_error_rate >= max(
        server_baseline + SERVER_ERROR_DELTA, SERVER_ERROR_FLOOR
    )
    error_anomaly = make_anomaly(
        AnomalyType.ERROR_SPIKE,
        EntityType.GLOBAL,
        "all-traffic",
        start,
        end,
        [err_z, err_cusum, err_seasonal, err_rule],
        base_metrics,
        thresholds,
        f"Error rate elevated to {error_rate * 100:.1f}% across the platform",
        ground_truth=ground_truth,
    )
    if error_anomaly and global_error_material:
        anomalies.append(error_anomaly)

    # ── Latency ──────────────────────────────────────────────────────────────
    latency_history = _series(history, "p95_response_time")
    lat_z = robust_zscore_detector(latency_history, p95, z_threshold, min_points, "up", "p95 latency")
    lat_ewma = ewma_detector(latency_history, p95, alpha, z_threshold, min_points, "p95 latency")
    lat_seasonal = seasonal_detector(history, p95, start, "p95_response_time", "window_start", 15, z_threshold, label="p95 latency")
    lat_baseline = float(np.median(latency_history)) if latency_history else p95
    multiplier = float(thresholds.get("latency_p95_multiplier", 2.5))
    lat_rule = _rule(
        score=soft_score(p95 / max(lat_baseline, 1.0), multiplier, sharpness=4.0),
        reason=f"p95 latency {p95:,.0f} ms is {p95 / max(lat_baseline, 1.0):.1f}x the {lat_baseline:,.0f} ms baseline",
        evidence={
            "p95_ms": round(p95, 1),
            "baseline_p95_ms": round(lat_baseline, 1),
            "p99_ms": round(_f(current, "p99_response_time"), 1),
            "multiplier_budget": multiplier,
        },
    )
    latency_anomaly = make_anomaly(
        AnomalyType.LATENCY_ANOMALY,
        EntityType.GLOBAL,
        "all-traffic",
        start,
        end,
        [lat_z, lat_ewma, lat_seasonal, lat_rule],
        base_metrics,
        thresholds,
        f"Latency degradation: p95 at {p95:,.0f} ms",
        ground_truth=ground_truth,
    )
    latency_material = _latency_is_material(p95, lat_baseline)
    if latency_anomaly and latency_material:
        anomalies.append(latency_anomaly)

    # ── Multivariate profile ─────────────────────────────────────────────────
    if history is not None and not history.empty:
        combined = pd.concat([history, current.to_frame().T], ignore_index=True)
        for column in GLOBAL_FEATURES:
            if column not in combined.columns:
                combined[column] = 0.0
        maha = mahalanobis_detector(history, current, GLOBAL_FEATURES, z_threshold, min_points=int(min_points * 2))
        iso = isolation_forest_detector(model, combined.tail(1)[GLOBAL_FEATURES], 0)
        best = max([maha, iso], key=lambda r: r.score)
        if best.score >= 0.55 and not anomalies:
            multivariate = make_anomaly(
                AnomalyType.MULTIVARIATE,
                EntityType.GLOBAL,
                "all-traffic",
                start,
                end,
                [maha, iso],
                base_metrics,
                thresholds,
                "Traffic profile does not match any recently observed pattern",
                ground_truth=ground_truth,
            )
            if multivariate:
                anomalies.append(multivariate)

    return anomalies


# ── Endpoints ────────────────────────────────────────────────────────────────
def analyze_endpoints(
    current: pd.DataFrame,
    history: pd.DataFrame,
    thresholds: Dict[str, float],
    min_requests: int = ENDPOINT_MIN_REQUESTS,
) -> List[Dict[str, Any]]:
    """Per-endpoint latency, error and access-pattern detections."""
    if current is None or current.empty:
        return []

    anomalies: List[Dict[str, Any]] = []
    z_threshold = float(thresholds.get("zscore_threshold", 3.5))
    # Full history requirement, not half: a six-window baseline has a MAD near
    # zero, which turns ordinary variation into a double-digit z-score.
    min_points = int(thresholds.get("min_history_points", 12))
    multiplier = float(thresholds.get("latency_p95_multiplier", 2.5))
    err_limit = float(thresholds.get("error_rate_threshold", 0.08))

    grouped = (
        history.groupby("endpoint") if history is not None and not history.empty and "endpoint" in history else None
    )

    # Traffic share, not absolute count.
    #
    # When platform traffic doubles, every endpoint's request count doubles with
    # it — that is the diurnal cycle, not an endpoint anomaly. Judging each
    # endpoint on its *share* of the window makes the signal invariant to the
    # overall level, so only a disproportionate change registers.
    current_totals = current.groupby("window_start")["requests"].sum()
    history_totals = (
        history.groupby("window_start")["requests"].sum()
        if history is not None and not history.empty and "window_start" in history
        else pd.Series(dtype=float)
    )

    for _, row in current.iterrows():
        endpoint = str(row.get("endpoint", "unknown"))
        requests = _f(row, "requests")
        if requests < min_requests:
            continue

        past = grouped.get_group(endpoint) if grouped is not None and endpoint in grouped.groups else pd.DataFrame()
        start, end = _window(row)
        p95 = _f(row, "p95_response_time")
        error_rate = _f(row, "error_rate")
        metrics = {
            "endpoint": endpoint,
            "method": row.get("method", "GET"),
            "requests": requests,
            "error_rate": error_rate,
            "p50_response_time": _f(row, "p50_response_time"),
            "p95_response_time": p95,
            "p99_response_time": _f(row, "p99_response_time"),
            "apdex": _f(row, "apdex"),
            "unique_ips": _f(row, "unique_ips"),
        }
        ground_truth = row.get("dominant_attack_label")

        # Latency
        latency_history = _series(past, "p95_response_time")
        baseline = float(np.median(latency_history)) if latency_history else p95
        lat_z = robust_zscore_detector(latency_history, p95, z_threshold, min_points, "up", f"{endpoint} p95")
        lat_ewma = ewma_detector(latency_history, p95, float(thresholds.get("ewma_alpha", 0.25)), z_threshold, min_points, f"{endpoint} p95")
        lat_rule = _rule(
            score=soft_score(p95 / max(baseline, 1.0), multiplier, sharpness=4.0),
            reason=f"p95 {p95:,.0f} ms vs {baseline:,.0f} ms endpoint baseline over {len(latency_history)} windows",
            evidence={"p95_ms": round(p95, 1), "baseline_ms": round(baseline, 1), "requests": requests},
        )
        # A fast endpoint getting slightly less fast is not an incident.
        latency_is_material = _latency_is_material(p95, baseline)
        slow = make_anomaly(
            AnomalyType.SLOW_ENDPOINT,
            EntityType.ENDPOINT,
            endpoint,
            start,
            end,
            [lat_z, lat_ewma, lat_rule],
            metrics,
            thresholds,
            f"{endpoint} responding slowly: p95 {p95:,.0f} ms over {requests:,.0f} requests",
            ground_truth=ground_truth,
            service=row.get("service"),
        )
        if slow and latency_is_material:
            anomalies.append(slow)

        # Errors
        error_history = _series(past, "server_error_rate") or _series(past, "error_rate")
        err_z = robust_zscore_detector(
            error_history, _f(row, "server_error_rate"), z_threshold, min_points, "up", f"{endpoint} 5xx rate"
        )
        err_cusum = cusum_detector(
            error_history, _f(row, "server_error_rate"), 0.5, 4.0, min_points, f"{endpoint} 5xx rate"
        )
        err_rule = _rule(
            score=soft_score(_f(row, "server_error_rate"), max(err_limit / 2, 0.02), sharpness=2.5),
            reason=(
                f"{_f(row, 'server_error_rate') * 100:.1f}% of {requests:,.0f} requests failed server-side "
                f"({int(_f(row, 'errors_5xx'))} 5xx); {error_rate * 100:.1f}% total error rate"
            ),
            evidence={
                "error_rate": round(error_rate, 4),
                "errors_4xx": int(_f(row, "errors_4xx")),
                "errors_5xx": int(_f(row, "errors_5xx")),
            },
        )
        server_error_rate = _f(row, "server_error_rate")
        server_history = _series(past, "server_error_rate")
        server_baseline = float(np.median(server_history)) if server_history else 0.0
        error_is_material = (
            requests >= RATE_MIN_SAMPLE
            and server_error_rate >= SERVER_ERROR_FLOOR
            and server_error_rate >= server_baseline + SERVER_ERROR_DELTA
        )
        errors = make_anomaly(
            AnomalyType.ERROR_SPIKE,
            EntityType.ENDPOINT,
            endpoint,
            start,
            end,
            [err_z, err_cusum, err_rule],
            metrics,
            thresholds,
            f"{endpoint} failing: {_f(row, 'server_error_rate') * 100:.1f}% server errors",
            ground_truth=ground_truth,
            service=row.get("service"),
        )
        if errors and error_is_material:
            anomalies.append(errors)

        # Unusual access pattern for this endpoint, measured as share of platform traffic.
        window_total = float(current_totals.get(row["window_start"], requests)) or requests
        share = requests / max(window_total, 1.0)

        share_history: List[float] = []
        if not past.empty and not history_totals.empty:
            for _, past_row in past.iterrows():
                total = float(history_totals.get(past_row["window_start"], 0.0))
                if total > 0:
                    share_history.append(float(past_row["requests"]) / total)

        vol_z = robust_zscore_detector(
            share_history, share, z_threshold + 0.5, min_points, "up", f"{endpoint} share of traffic"
        )
        vol_seasonal = DetectionResult.neutral(Detector.SEASONAL)
        share_baseline = float(np.median(share_history)) if share_history else share
        # A share change is only meaningful if the endpoint is also carrying
        # enough traffic for the ratio to be stable.
        share_is_material = (
            requests >= RATE_MIN_SAMPLE and share >= max(share_baseline * 1.5, share_baseline + 0.03)
        )
        metrics["traffic_share"] = round(share, 5)
        metrics["baseline_share"] = round(share_baseline, 5)

        unusual = make_anomaly(
            AnomalyType.TRAFFIC_SPIKE,
            EntityType.ENDPOINT,
            endpoint,
            start,
            end,
            [vol_z, vol_seasonal],
            metrics,
            thresholds,
            f"Unusual access pattern on {endpoint}: {share * 100:.1f}% of platform traffic "
            f"({requests:,.0f} requests) against a {share_baseline * 100:.1f}% baseline",
            ground_truth=ground_truth,
            service=row.get("service"),
        )
        if unusual and share_is_material:
            anomalies.append(unusual)

    return anomalies


# ── IP behaviour ─────────────────────────────────────────────────────────────
def analyze_ips(
    current: pd.DataFrame,
    history: pd.DataFrame,
    thresholds: Dict[str, float],
    model: Optional[ModelBundle] = None,
    top_n: int = 400,
) -> List[Dict[str, Any]]:
    """Per-source-IP behavioural detection.

    Combines an unsupervised model over the whole IP population in this window
    with targeted rules for the three attack shapes that matter operationally:
    volumetric abuse, endpoint scanning and credential stuffing.
    """
    if current is None or current.empty:
        return []

    frame = current.copy()
    frame = frame.sort_values("requests", ascending=False).head(top_n).reset_index(drop=True)
    for column in IP_FEATURES:
        if column not in frame.columns:
            frame[column] = 0.0
    frame[IP_FEATURES] = frame[IP_FEATURES].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    population = frame[IP_FEATURES]

    # Volume floor for model-only detections.
    #
    # In a heavy-tailed population, *being in the tail is normal*: every window
    # contains clients with one request and a 100% error ratio, and an
    # unsupervised outlier model will happily rank them as extreme. Those are
    # statistically unusual and operationally meaningless, and flagging them
    # critical is how a detector loses its audience.
    #
    # Rules with an explicit operational meaning (flood, scan, auth abuse) are
    # NOT gated by this — they carry their own evidence. Only the catch-all is.
    median_requests = float(np.median(frame["requests"])) if len(frame) else 0.0
    volume_floor = max(30.0, median_requests * 3.0)

    rps_limit = float(thresholds.get("ip_rps_threshold", 25))
    scan_paths = float(thresholds.get("scan_unique_paths", 30))
    auth_limit = float(thresholds.get("auth_fail_threshold", 15))
    z_threshold = float(thresholds.get("zscore_threshold", 3.5))

    anomalies: List[Dict[str, Any]] = []
    for index, row in frame.iterrows():
        ip = str(row.get("ip", "0.0.0.0"))
        requests = _f(row, "requests")
        if requests < 5:
            continue
        start, end = _window(row)
        window_seconds = max((end - start).total_seconds(), 1.0)
        ip_rps = requests / window_seconds
        unique_paths = _f(row, "unique_paths")
        not_found_ratio = _f(row, "not_found_ratio")
        auth_fail = _f(row, "auth_fail_count")
        error_ratio = _f(row, "error_ratio")
        entropy = _f(row, "path_entropy")
        burstiness = _f(row, "burstiness")

        metrics = {
            "ip": ip,
            "requests": requests,
            "rps": round(ip_rps, 2),
            "unique_paths": unique_paths,
            "unique_user_agents": _f(row, "unique_user_agents"),
            "error_ratio": error_ratio,
            "not_found_ratio": not_found_ratio,
            "auth_fail_count": auth_fail,
            "avg_response_time": _f(row, "avg_response_time"),
            "country": row.get("country", "??"),
            "asn": row.get("asn", "AS0"),
            "org": row.get("org", "unknown"),
            "bytes_sent": _f(row, "bytes_sent"),
            "path_entropy": entropy,
            "burstiness": burstiness,
        }
        ground_truth = row.get("dominant_attack_label")

        iso = isolation_forest_detector(model, frame, index) if model is not None and model.ready else DetectionResult.neutral(Detector.ISOLATION_FOREST, "model not trained yet")
        maha = mahalanobis_detector(population, row, IP_FEATURES, z_threshold, min_points=20)

        # Volumetric abuse
        volume_rule = _rule(
            score=soft_score(ip_rps, rps_limit, sharpness=3.0),
            reason=f"{ip} issued {requests:,.0f} requests ({ip_rps:.1f} rps) in one window",
            evidence={"requests": requests, "rps": round(ip_rps, 2), "limit_rps": rps_limit, "burstiness": round(burstiness, 2)},
        )
        if volume_rule.score >= 0.4:
            volumetric = make_anomaly(
                AnomalyType.DDOS_BURST if ip_rps >= rps_limit * 2 else AnomalyType.SUSPICIOUS_IP,
                EntityType.IP,
                ip,
                start,
                end,
                [volume_rule, iso, maha],
                metrics,
                thresholds,
                f"{ip} is flooding the platform at {ip_rps:.0f} rps from {metrics['country']}",
                ground_truth=ground_truth,
            )
            if volumetric:
                anomalies.append(volumetric)

        # Endpoint scanning: many distinct paths, high 404 share, high entropy
        scan_score = float(
            np.cbrt(
                max(soft_score(unique_paths, scan_paths, sharpness=3.0), 1e-6)
                * max(soft_score(not_found_ratio, 0.35, sharpness=3.0), 1e-6)
                * max(soft_score(entropy, 0.6, sharpness=4.0), 1e-6)
            )
        )
        if scan_score >= 0.35:
            scan_rule = _rule(
                score=scan_score,
                reason=(
                    f"{ip} touched {unique_paths:,.0f} distinct paths with {not_found_ratio * 100:.0f}% 404s "
                    f"and {entropy:.2f} normalised path entropy — consistent with directory enumeration"
                ),
                evidence={
                    "unique_paths": unique_paths,
                    "not_found_ratio": round(not_found_ratio, 3),
                    "path_entropy": round(entropy, 3),
                    "sample_paths": row.get("sample_paths", [])[:8] if isinstance(row.get("sample_paths"), list) else [],
                },
            )
            scan = make_anomaly(
                AnomalyType.ENDPOINT_SCAN,
                EntityType.IP,
                ip,
                start,
                end,
                [scan_rule, iso, maha],
                metrics,
                thresholds,
                f"{ip} is enumerating endpoints ({unique_paths:,.0f} paths, {not_found_ratio * 100:.0f}% 404)",
                ground_truth=ground_truth,
            )
            if scan:
                anomalies.append(scan)

        # Credential stuffing
        if auth_fail >= max(auth_limit * 0.5, 3):
            auth_rule = _rule(
                score=soft_score(auth_fail, auth_limit, sharpness=2.5),
                reason=f"{int(auth_fail)} authentication failures from {ip} within one minute",
                evidence={
                    "auth_fail_count": int(auth_fail),
                    "threshold": auth_limit,
                    "unique_paths": unique_paths,
                    "error_ratio": round(error_ratio, 3),
                },
            )
            auth = make_anomaly(
                AnomalyType.AUTH_ABUSE,
                EntityType.IP,
                ip,
                start,
                end,
                [auth_rule, iso],
                metrics,
                thresholds,
                f"Credential stuffing from {ip}: {int(auth_fail)} failed logins",
                ground_truth=ground_truth,
            )
            if auth:
                anomalies.append(auth)

        # Scraping: sustained volume, narrow path set, large payloads
        if requests >= 60 and entropy <= 0.35 and unique_paths <= 6:
            scrape_rule = _rule(
                score=float(min(0.95, soft_score(requests, 120, sharpness=3.0) * 0.7 + 0.3)),
                reason=(
                    f"{ip} pulled {requests:,.0f} requests across only {unique_paths:.0f} paths "
                    f"({_f(row, 'bytes_sent') / max(requests, 1) / 1024:.1f} KB per request) — bulk extraction pattern"
                ),
                evidence={
                    "requests": requests,
                    "unique_paths": unique_paths,
                    "path_entropy": round(entropy, 3),
                    "kb_per_request": round(_f(row, "bytes_sent") / max(requests, 1) / 1024, 2),
                },
            )
            scrape = make_anomaly(
                AnomalyType.DATA_SCRAPING,
                EntityType.IP,
                ip,
                start,
                end,
                [scrape_rule, iso, maha],
                metrics,
                thresholds,
                f"Bulk data extraction from {ip}",
                ground_truth=ground_truth,
            )
            if scrape:
                anomalies.append(scrape)

        # Pure model-driven catch-all for shapes no rule anticipated.
        # Requires meaningful volume and a strong model score, because there is
        # no corroborating rule to justify the alert.
        if requests >= volume_floor and not any(
            a["entity"] == ip and a["window_start"] == start for a in anomalies
        ):
            if max(iso.score, maha.score) >= 0.85:
                unknown = make_anomaly(
                    AnomalyType.SUSPICIOUS_IP,
                    EntityType.IP,
                    ip,
                    start,
                    end,
                    [iso, maha],
                    metrics,
                    thresholds,
                    f"{ip} behaves unlike the rest of the traffic population",
                    ground_truth=ground_truth,
                )
                if unknown:
                    anomalies.append(unknown)

    return anomalies


# ── Geography ────────────────────────────────────────────────────────────────
def analyze_geo(
    current: pd.DataFrame,
    history: pd.DataFrame,
    thresholds: Dict[str, float],
    min_requests: int = 50,
) -> List[Dict[str, Any]]:
    """Country-level volume and error shifts (new-origin and surge detection)."""
    if current is None or current.empty:
        return []

    anomalies: List[Dict[str, Any]] = []
    z_threshold = float(thresholds.get("zscore_threshold", 3.5)) + 0.5
    grouped = history.groupby("country") if history is not None and not history.empty and "country" in history else None

    # An established origin's absolute volume rises and falls with the daily
    # cycle, so a rolling baseline flags India every morning. Its *share* of
    # traffic is stable — and share is also what a geographic anomaly actually
    # means: "this origin is now a much bigger slice of our traffic than usual".
    current_totals = current.groupby("window_start")["requests"].sum()
    history_totals = (
        history.groupby("window_start")["requests"].sum()
        if history is not None and not history.empty and "window_start" in history
        else pd.Series(dtype=float)
    )

    for _, row in current.iterrows():
        country = str(row.get("country", "??"))
        requests = _f(row, "requests")
        if requests < min_requests:
            continue
        start, end = _window(row)
        past = grouped.get_group(country) if grouped is not None and country in grouped.groups else pd.DataFrame()

        window_total = float(current_totals.get(row["window_start"], requests)) or requests
        share = requests / max(window_total, 1.0)
        share_history: List[float] = []
        if not past.empty and not history_totals.empty:
            for _, past_row in past.iterrows():
                total = float(history_totals.get(past_row["window_start"], 0.0))
                if total > 0:
                    share_history.append(float(past_row["requests"]) / total)

        # No history means the origin was not previously sending traffic, so its
        # baseline share is zero. Defaulting to the *current* share instead made
        # every brand-new origin definitionally immaterial — the exact case this
        # detector exists for.
        share_baseline = float(np.median(share_history)) if share_history else 0.0
        detectors: List[DetectionResult] = [
            robust_zscore_detector(
                share_history, share, z_threshold, 8, "up", f"share of traffic from {country}"
            )
        ]
        if len(share_history) < 3 and requests >= min_requests * 3:
            detectors.append(
                _rule(
                    score=0.7,
                    reason=f"{country} has no meaningful traffic history but is now sending {requests:,.0f} requests",
                    evidence={"requests": requests, "history_windows": len(share_history), "new_origin": True},
                )
            )
        # A share shift only matters if the origin is materially bigger than usual.
        #
        # The floor is deliberately small in absolute terms. Injected origin
        # shifts measured on real data reach only 1-4% of platform traffic, and
        # a hostile network going from ~0% to 3% is an enormous *relative*
        # change even though it is a small slice. Precision here comes from the
        # z-score on the share series — an established origin's share barely
        # moves through the daily cycle — not from a large absolute threshold,
        # which only suppressed every real detection.
        share_is_material = share >= max(share_baseline * 1.5, share_baseline + 0.01)

        anomaly = make_anomaly(
            AnomalyType.GEO_ANOMALY,
            EntityType.COUNTRY,
            country,
            start,
            end,
            detectors,
            {
                "country": country,
                "country_name": row.get("country_name", country),
                "requests": requests,
                "error_rate": _f(row, "error_rate"),
                "unique_ips": _f(row, "unique_ips"),
                "p95_response_time": _f(row, "p95_response_time"),
                "traffic_share": round(share, 5),
                "baseline_share": round(share_baseline, 5),
            },
            thresholds,
            f"Unusual traffic origin: {row.get('country_name', country)} is {share * 100:.1f}% of platform "
            f"traffic ({requests:,.0f} requests) against a {share_baseline * 100:.1f}% baseline",
            ground_truth=row.get("dominant_attack_label"),
        )
        if anomaly and share_is_material:
            anomalies.append(anomaly)

    return anomalies


# ── Sessions ─────────────────────────────────────────────────────────────────
def analyze_sessions(
    sessions: pd.DataFrame,
    thresholds: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Abnormal user-session behaviour (velocity, error load, breadth)."""
    if sessions is None or sessions.empty:
        return []

    frame = sessions.copy()
    features = ["requests", "duration_seconds", "error_count", "unique_endpoints", "requests_per_minute"]
    for column in features:
        if column not in frame.columns:
            frame[column] = 0.0
    frame[features] = frame[features].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    anomalies: List[Dict[str, Any]] = []
    for index, row in frame.iterrows():
        requests = _f(row, "requests")
        if requests < 15:
            continue
        rpm = _f(row, "requests_per_minute")
        errors = _f(row, "error_count")
        endpoints = _f(row, "unique_endpoints")
        session_id = str(row.get("session_id", "unknown"))
        start, end = _window(
            {"window_start": row.get("session_start"), "window_end": row.get("session_end")}
        )

        maha = mahalanobis_detector(frame[features], row, features, float(thresholds.get("zscore_threshold", 3.5)), 20)
        velocity = _rule(
            score=soft_score(rpm, 90, sharpness=3.0),
            reason=f"session sustained {rpm:,.0f} requests/minute across {endpoints:,.0f} endpoints",
            evidence={
                "requests_per_minute": round(rpm, 1),
                "unique_endpoints": endpoints,
                "duration_seconds": round(_f(row, "duration_seconds"), 1),
                "error_count": int(errors),
            },
        )
        anomaly = make_anomaly(
            AnomalyType.SESSION_ANOMALY,
            EntityType.SESSION,
            session_id,
            start,
            end,
            [velocity, maha],
            {
                "session_id": session_id,
                "user_id": row.get("user_id"),
                "ip": row.get("ip"),
                "requests": requests,
                "requests_per_minute": rpm,
                "unique_endpoints": endpoints,
                "error_count": errors,
                "duration_seconds": _f(row, "duration_seconds"),
            },
            thresholds,
            f"Abnormal session {session_id}: {requests:,.0f} requests at {rpm:,.0f} rpm",
            ground_truth=row.get("dominant_attack_label"),
        )
        if anomaly:
            anomalies.append(anomaly)

    return anomalies


def dedupe(anomalies: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the highest-scoring anomaly per (id) — deterministic ids collide by design."""
    best: Dict[str, Dict[str, Any]] = {}
    for anomaly in anomalies:
        key = anomaly["anomaly_id"]
        if key not in best or anomaly["score"] > best[key]["score"]:
            best[key] = anomaly
    return sorted(best.values(), key=lambda a: a["score"], reverse=True)
