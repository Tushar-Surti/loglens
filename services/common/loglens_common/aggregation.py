"""Reference (pandas) implementation of every windowed metric document.

This module is the **contract** for what a metric document contains.  Spark
computes the same fields in a distributed fashion for the live stream; this
version is used for historical backfill, offline replay, evaluation and tests,
where a single process is the right tool.

Keeping one authoritative definition means the historical charts and the live
charts are genuinely the same measurements.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .detectors import gini, normalized_entropy

APDEX_TARGET_MS = 500.0
AUTH_PATH_MARKER = "/auth/"


def _percentiles(series: pd.Series) -> Dict[str, float]:
    if series.empty:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "max": 0.0}
    values = series.to_numpy(dtype=float)
    p50, p90, p95, p99 = np.percentile(values, [50, 90, 95, 99])
    return {
        "p50": float(p50),
        "p90": float(p90),
        "p95": float(p95),
        "p99": float(p99),
        "avg": float(values.mean()),
        "max": float(values.max()),
    }


def _apdex(series: pd.Series, target_ms: float = APDEX_TARGET_MS) -> float:
    if series.empty:
        return 1.0
    values = series.to_numpy(dtype=float)
    satisfied = float((values <= target_ms).sum())
    tolerating = float(((values > target_ms) & (values <= target_ms * 4)).sum())
    return round((satisfied + tolerating / 2.0) / len(values), 4)


def _dominant_label(series: pd.Series) -> Optional[str]:
    labels = series.dropna()
    if labels.empty:
        return None
    return str(labels.value_counts().idxmax())


def burstiness_from_span(requests: int, span_seconds: float, window_seconds: float = 60.0) -> float:
    """Temporal concentration: 1.0 = evenly spread, 20.0 = a one-window burst.

    Defined as ``window / active_span``.  Deliberately computable from just
    ``min(ts)``/``max(ts)`` so the streaming aggregation in Spark and this
    reference implementation produce *identical* features — the IsolationForest
    is trained on backfill output and scored on streaming output, so any
    definition drift between the two would silently poison the model.
    """
    if requests < 3 or span_seconds <= 0:
        return 1.0
    return float(min(window_seconds / max(span_seconds, 1.0), window_seconds))


def _burstiness(timestamps: pd.Series, window_seconds: float = 60.0) -> float:
    if timestamps.empty or len(timestamps) < 3:
        return 1.0
    span = (timestamps.max() - timestamps.min()).total_seconds()
    return burstiness_from_span(len(timestamps), span, window_seconds)


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalise a raw-event frame: types, derived columns, sane defaults."""
    df = frame.copy()
    defaults = {
        "bytes_sent": 0,
        "is_bot": False,
        "cache_status": "MISS",
        "attack_label": None,
        "user_id": None,
        "session_id": "-",
        "service": "unknown",
        "country": "??",
        "path": "/",
        "endpoint": "/",
        "method": "GET",
        "user_agent": "-",
        "ip": "0.0.0.0",
    }
    for column, default in defaults.items():
        if column not in df.columns:
            df[column] = default

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["status"] = pd.to_numeric(df["status"], errors="coerce").fillna(0).astype(int)
    df["response_time_ms"] = pd.to_numeric(df["response_time_ms"], errors="coerce").fillna(0.0)
    df["bytes_sent"] = pd.to_numeric(df["bytes_sent"], errors="coerce").fillna(0).astype("int64")
    df["is_bot"] = df["is_bot"].fillna(False).astype(bool)
    df["status_class"] = (df["status"] // 100).astype(int).astype(str) + "xx"
    df["is_error"] = df["status"] >= 400
    df["is_client_error"] = (df["status"] >= 400) & (df["status"] < 500)
    df["is_server_error"] = df["status"] >= 500
    df["is_not_found"] = df["status"] == 404
    df["is_auth_fail"] = (df["status"].isin([401, 403])) & (
        df["path"].astype(str).str.contains(AUTH_PATH_MARKER, regex=False)
    )
    df["cache_hit"] = df["cache_status"].astype(str).eq("HIT")
    return df


def _window_bounds(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    df = df.copy()
    df["window_start"] = df["timestamp"].dt.floor(freq)
    df["window_end"] = df["window_start"] + pd.tseries.frequencies.to_offset(freq)
    return df


# ── Global ───────────────────────────────────────────────────────────────────
def global_metrics(df: pd.DataFrame, freq: str = "1min") -> List[Dict[str, Any]]:
    if df.empty:
        return []
    df = _window_bounds(df, freq)
    seconds = pd.tseries.frequencies.to_offset(freq).nanos / 1e9
    docs: List[Dict[str, Any]] = []

    for (start, end), group in df.groupby(["window_start", "window_end"], sort=True):
        requests = len(group)
        latency = _percentiles(group["response_time_ms"])
        ip_counts = group["ip"].value_counts()
        path_counts = group["endpoint"].value_counts()
        errors_4xx = int(group["is_client_error"].sum())
        errors_5xx = int(group["is_server_error"].sum())
        bytes_sent = int(group["bytes_sent"].sum())
        bots = int(group["is_bot"].sum())
        cache_hits = int(group["cache_hit"].sum())

        docs.append(
            {
                "window_start": start.to_pydatetime(),
                "window_end": end.to_pydatetime(),
                "requests": requests,
                "rps": round(requests / seconds, 3),
                "unique_ips": int(group["ip"].nunique()),
                "unique_sessions": int(group["session_id"].nunique()),
                "unique_users": int(group["user_id"].dropna().nunique()),
                "unique_endpoints": int(group["endpoint"].nunique()),
                "errors_4xx": errors_4xx,
                "errors_5xx": errors_5xx,
                "error_rate": round((errors_4xx + errors_5xx) / requests, 5),
                "server_error_rate": round(errors_5xx / requests, 5),
                "success_rate": round(1.0 - (errors_4xx + errors_5xx) / requests, 5),
                "bytes_sent": bytes_sent,
                "bytes_per_request": round(bytes_sent / requests, 2),
                "avg_response_time": round(latency["avg"], 2),
                "p50_response_time": round(latency["p50"], 2),
                "p90_response_time": round(latency["p90"], 2),
                "p95_response_time": round(latency["p95"], 2),
                "p99_response_time": round(latency["p99"], 2),
                "max_response_time": round(latency["max"], 2),
                "apdex": _apdex(group["response_time_ms"]),
                "bot_requests": bots,
                "bot_ratio": round(bots / requests, 4),
                "cache_hit_rate": round(cache_hits / requests, 4),
                "ip_gini": round(gini(ip_counts.tolist()), 4),
                "path_entropy": round(normalized_entropy(path_counts.tolist()), 4),
                "top_ip": str(ip_counts.index[0]) if len(ip_counts) else None,
                "top_ip_share": round(float(ip_counts.iloc[0]) / requests, 4) if len(ip_counts) else 0.0,
                "status_counts": {str(k): int(v) for k, v in group["status"].value_counts().items()},
                "method_counts": {str(k): int(v) for k, v in group["method"].value_counts().items()},
                "dominant_attack_label": _dominant_label(group.get("attack_label", pd.Series(dtype=object))),
                "updated_at": pd.Timestamp.utcnow().to_pydatetime(),
            }
        )
    return docs


# ── Endpoints ────────────────────────────────────────────────────────────────
def endpoint_metrics(df: pd.DataFrame, freq: str = "1min") -> List[Dict[str, Any]]:
    if df.empty:
        return []
    df = _window_bounds(df, freq)
    docs: List[Dict[str, Any]] = []
    for (start, end, endpoint, method), group in df.groupby(
        ["window_start", "window_end", "endpoint", "method"], sort=False
    ):
        requests = len(group)
        latency = _percentiles(group["response_time_ms"])
        errors_4xx = int(group["is_client_error"].sum())
        errors_5xx = int(group["is_server_error"].sum())
        docs.append(
            {
                "window_start": start.to_pydatetime(),
                "window_end": end.to_pydatetime(),
                "endpoint": endpoint,
                "method": method,
                "service": str(group["service"].mode().iloc[0]) if "service" in group else "unknown",
                "requests": requests,
                "errors_4xx": errors_4xx,
                "errors_5xx": errors_5xx,
                "error_rate": round((errors_4xx + errors_5xx) / requests, 5),
                "server_error_rate": round(errors_5xx / requests, 5),
                "avg_response_time": round(latency["avg"], 2),
                "p50_response_time": round(latency["p50"], 2),
                "p90_response_time": round(latency["p90"], 2),
                "p95_response_time": round(latency["p95"], 2),
                "p99_response_time": round(latency["p99"], 2),
                "max_response_time": round(latency["max"], 2),
                "apdex": _apdex(group["response_time_ms"]),
                "unique_ips": int(group["ip"].nunique()),
                "unique_sessions": int(group["session_id"].nunique()),
                "bytes_sent": int(group["bytes_sent"].sum()),
                "cache_hit_rate": round(float(group["cache_hit"].mean()), 4),
                "dominant_attack_label": _dominant_label(group.get("attack_label", pd.Series(dtype=object))),
                "updated_at": pd.Timestamp.utcnow().to_pydatetime(),
            }
        )
    return docs


# ── Source IPs ───────────────────────────────────────────────────────────────
def ip_metrics(df: pd.DataFrame, freq: str = "1min", min_requests: int = 1) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    df = _window_bounds(df, freq)
    seconds = pd.tseries.frequencies.to_offset(freq).nanos / 1e9
    docs: List[Dict[str, Any]] = []
    for (start, end, ip), group in df.groupby(["window_start", "window_end", "ip"], sort=False):
        requests = len(group)
        if requests < min_requests:
            continue
        # Grouped on the *normalised* endpoint, not the concrete path: dynamic
        # ids would otherwise make every request look like a unique path and
        # destroy the scanning signal.  Scanner probes are their own endpoints.
        path_counts = group["endpoint"].value_counts()
        bytes_sent = int(group["bytes_sent"].sum())
        docs.append(
            {
                "window_start": start.to_pydatetime(),
                "window_end": end.to_pydatetime(),
                "ip": ip,
                "requests": requests,
                "rps": round(requests / seconds, 3),
                "unique_paths": int(group["endpoint"].nunique()),
                "unique_endpoints": int(group["endpoint"].nunique()),
                "unique_user_agents": int(group["user_agent"].nunique()),
                "unique_sessions": int(group["session_id"].nunique()),
                "error_ratio": round(float(group["is_error"].mean()), 4),
                "not_found_ratio": round(float(group["is_not_found"].mean()), 4),
                "server_error_count": int(group["is_server_error"].sum()),
                "auth_fail_count": int(group["is_auth_fail"].sum()),
                "avg_response_time": round(float(group["response_time_ms"].mean()), 2),
                "max_response_time": round(float(group["response_time_ms"].max()), 2),
                "bytes_sent": bytes_sent,
                "bytes_per_request": round(bytes_sent / requests, 2),
                "path_entropy": round(normalized_entropy(path_counts.tolist()), 4),
                "burstiness": round(_burstiness(group["timestamp"], seconds), 3),
                "bot_ratio": round(float(group["is_bot"].mean()), 4),
                "country": str(group["country"].mode().iloc[0]) if "country" in group else "??",
                "asn": str(group["asn"].mode().iloc[0]) if "asn" in group else "AS0",
                "org": str(group["org"].mode().iloc[0]) if "org" in group else "unknown",
                "methods": {str(k): int(v) for k, v in group["method"].value_counts().items()},
                "sample_paths": [str(p) for p in path_counts.index[:10]],
                "dominant_attack_label": _dominant_label(group.get("attack_label", pd.Series(dtype=object))),
                "updated_at": pd.Timestamp.utcnow().to_pydatetime(),
            }
        )
    return docs


# ── Status codes ─────────────────────────────────────────────────────────────
def status_metrics(df: pd.DataFrame, freq: str = "1min") -> List[Dict[str, Any]]:
    if df.empty:
        return []
    df = _window_bounds(df, freq)
    docs: List[Dict[str, Any]] = []
    for (start, end, status_class), group in df.groupby(
        ["window_start", "window_end", "status_class"], sort=False
    ):
        docs.append(
            {
                "window_start": start.to_pydatetime(),
                "window_end": end.to_pydatetime(),
                "status_class": status_class,
                "requests": len(group),
                "codes": {str(k): int(v) for k, v in group["status"].value_counts().items()},
                "avg_response_time": round(float(group["response_time_ms"].mean()), 2),
                "top_endpoints": [
                    {"endpoint": str(k), "requests": int(v)}
                    for k, v in group["endpoint"].value_counts().head(5).items()
                ],
                "updated_at": pd.Timestamp.utcnow().to_pydatetime(),
            }
        )
    return docs


# ── Geography ────────────────────────────────────────────────────────────────
def geo_metrics(df: pd.DataFrame, freq: str = "5min") -> List[Dict[str, Any]]:
    if df.empty:
        return []
    df = _window_bounds(df, freq)
    docs: List[Dict[str, Any]] = []
    for (start, end, country), group in df.groupby(["window_start", "window_end", "country"], sort=False):
        requests = len(group)
        docs.append(
            {
                "window_start": start.to_pydatetime(),
                "window_end": end.to_pydatetime(),
                "country": country,
                "country_name": str(group["country_name"].mode().iloc[0]) if "country_name" in group else country,
                "requests": requests,
                "unique_ips": int(group["ip"].nunique()),
                "unique_sessions": int(group["session_id"].nunique()),
                "error_rate": round(float(group["is_error"].mean()), 5),
                "avg_response_time": round(float(group["response_time_ms"].mean()), 2),
                "p95_response_time": round(float(np.percentile(group["response_time_ms"], 95)), 2),
                "bytes_sent": int(group["bytes_sent"].sum()),
                "lat": round(float(group["lat"].mean()), 4) if "lat" in group else 0.0,
                "lon": round(float(group["lon"].mean()), 4) if "lon" in group else 0.0,
                "top_cities": [
                    {"city": str(k), "requests": int(v)}
                    for k, v in group["city"].value_counts().head(4).items()
                ]
                if "city" in group
                else [],
                "dominant_attack_label": _dominant_label(group.get("attack_label", pd.Series(dtype=object))),
                "updated_at": pd.Timestamp.utcnow().to_pydatetime(),
            }
        )
    return docs


# ── Services ─────────────────────────────────────────────────────────────────
def service_metrics(df: pd.DataFrame, freq: str = "1min") -> List[Dict[str, Any]]:
    if df.empty or "service" not in df:
        return []
    df = _window_bounds(df, freq)
    docs: List[Dict[str, Any]] = []
    for (start, end, service), group in df.groupby(["window_start", "window_end", "service"], sort=False):
        requests = len(group)
        latency = _percentiles(group["response_time_ms"])
        docs.append(
            {
                "window_start": start.to_pydatetime(),
                "window_end": end.to_pydatetime(),
                "service": service,
                "requests": requests,
                "error_rate": round(float(group["is_error"].mean()), 5),
                "server_error_rate": round(float(group["is_server_error"].mean()), 5),
                "p95_response_time": round(latency["p95"], 2),
                "p99_response_time": round(latency["p99"], 2),
                "avg_response_time": round(latency["avg"], 2),
                "apdex": _apdex(group["response_time_ms"]),
                "hosts": int(group["host"].nunique()) if "host" in group else 0,
                "updated_at": pd.Timestamp.utcnow().to_pydatetime(),
            }
        )
    return docs


# ── Sessions ─────────────────────────────────────────────────────────────────
def session_metrics(df: pd.DataFrame, min_requests: int = 1) -> List[Dict[str, Any]]:
    if df.empty or "session_id" not in df:
        return []
    docs: List[Dict[str, Any]] = []
    for session_id, group in df.groupby("session_id", sort=False):
        requests = len(group)
        if requests < min_requests:
            continue
        group = group.sort_values("timestamp")
        start = group["timestamp"].iloc[0]
        end = group["timestamp"].iloc[-1]
        duration = max((end - start).total_seconds(), 1.0)
        docs.append(
            {
                "session_id": str(session_id),
                "session_start": start.to_pydatetime(),
                "session_end": end.to_pydatetime(),
                "window_start": start.to_pydatetime(),
                "window_end": end.to_pydatetime(),
                "duration_seconds": round(duration, 2),
                "requests": requests,
                "requests_per_minute": round(requests / (duration / 60.0), 2),
                "unique_endpoints": int(group["endpoint"].nunique()),
                "error_count": int(group["is_error"].sum()),
                "bytes_sent": int(group["bytes_sent"].sum()),
                "user_id": str(group["user_id"].dropna().iloc[0]) if group["user_id"].notna().any() else None,
                "ip": str(group["ip"].mode().iloc[0]),
                "country": str(group["country"].mode().iloc[0]) if "country" in group else "??",
                "device": str(group["device"].mode().iloc[0]) if "device" in group else "desktop",
                "browser": str(group["browser"].mode().iloc[0]) if "browser" in group else "unknown",
                "is_bot": bool(group["is_bot"].mean() > 0.5),
                "entry_endpoint": str(group["endpoint"].iloc[0]),
                "exit_endpoint": str(group["endpoint"].iloc[-1]),
                "converted": bool(group["endpoint"].str.contains("checkout|payments", regex=True).any()),
                "avg_response_time": round(float(group["response_time_ms"].mean()), 2),
                "dominant_attack_label": _dominant_label(group.get("attack_label", pd.Series(dtype=object))),
                "updated_at": pd.Timestamp.utcnow().to_pydatetime(),
            }
        )
    return docs


def all_metrics(df: pd.DataFrame, metric_freq: str = "1min", geo_freq: str = "5min") -> Dict[str, List[Dict[str, Any]]]:
    """Compute every metric family from one prepared event frame."""
    prepared = prepare(df)
    return {
        "global": global_metrics(prepared, metric_freq),
        "endpoint": endpoint_metrics(prepared, metric_freq),
        "ip": ip_metrics(prepared, metric_freq),
        "status": status_metrics(prepared, metric_freq),
        "geo": geo_metrics(prepared, geo_freq),
        "service": service_metrics(prepared, metric_freq),
        "session": session_metrics(prepared),
    }
