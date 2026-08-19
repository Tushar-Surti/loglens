"""Overview KPIs, time series and traffic-pattern analytics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from loglens_common.schemas import Collections

from ..deps import TimeRange, db, drop_open_bucket, time_range
from ..serialization import docs, jsonable

router = APIRouter(tags=["overview"])

#: Aggregation recipe per metric.  Rates are re-derived from their numerator and
#: denominator rather than averaged, because averaging a ratio over unequal
#: buckets is simply wrong.
METRIC_SPECS: Dict[str, Dict[str, Any]] = {
    "requests": {"op": "sum", "field": "requests", "label": "Requests", "unit": "req"},
    "rps": {"op": "rate", "field": "requests", "label": "Requests / sec", "unit": "rps"},
    "error_rate": {"op": "ratio", "numerator": ["errors_4xx", "errors_5xx"], "denominator": "requests",
                   "label": "Error rate", "unit": "%"},
    "server_error_rate": {"op": "ratio", "numerator": ["errors_5xx"], "denominator": "requests",
                          "label": "5xx rate", "unit": "%"},
    "errors_4xx": {"op": "sum", "field": "errors_4xx", "label": "4xx", "unit": "req"},
    "errors_5xx": {"op": "sum", "field": "errors_5xx", "label": "5xx", "unit": "req"},
    "p50_response_time": {"op": "avg", "field": "p50_response_time", "label": "p50 latency", "unit": "ms"},
    "p90_response_time": {"op": "avg", "field": "p90_response_time", "label": "p90 latency", "unit": "ms"},
    "p95_response_time": {"op": "avg", "field": "p95_response_time", "label": "p95 latency", "unit": "ms"},
    "p99_response_time": {"op": "avg", "field": "p99_response_time", "label": "p99 latency", "unit": "ms"},
    "avg_response_time": {"op": "avg", "field": "avg_response_time", "label": "Average latency", "unit": "ms"},
    "unique_ips": {"op": "avg", "field": "unique_ips", "label": "Unique IPs", "unit": ""},
    "unique_sessions": {"op": "avg", "field": "unique_sessions", "label": "Active sessions", "unit": ""},
    "bytes_sent": {"op": "sum", "field": "bytes_sent", "label": "Bytes served", "unit": "B"},
    "apdex": {"op": "avg", "field": "apdex", "label": "Apdex", "unit": ""},
    "bot_ratio": {"op": "avg", "field": "bot_ratio", "label": "Bot share", "unit": "%"},
    "cache_hit_rate": {"op": "avg", "field": "cache_hit_rate", "label": "Cache hit rate", "unit": "%"},
    "ip_gini": {"op": "avg", "field": "ip_gini", "label": "Source concentration", "unit": ""},
    "path_entropy": {"op": "avg", "field": "path_entropy", "label": "Path entropy", "unit": ""},
}


def _bucket_stage(bucket_seconds: int) -> Dict[str, Any]:
    return {"$dateTrunc": {"date": "$window_start", "unit": "second", "binSize": bucket_seconds}}


async def _series(database, window: TimeRange, metrics: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    accumulators: Dict[str, Any] = {"requests": {"$sum": "$requests"}}
    for metric in metrics:
        spec = METRIC_SPECS.get(metric)
        if not spec:
            continue
        if spec["op"] in ("sum", "rate"):
            accumulators[f"m_{metric}"] = {"$sum": f"${spec['field']}"}
        elif spec["op"] == "avg":
            accumulators[f"m_{metric}"] = {"$avg": f"${spec['field']}"}
        elif spec["op"] == "ratio":
            for field in spec["numerator"]:
                accumulators[f"n_{field}"] = {"$sum": f"${field}"}
            accumulators[f"d_{spec['denominator']}"] = {"$sum": f"${spec['denominator']}"}

    pipeline = [
        {"$match": window.filter()},
        {"$group": {"_id": _bucket_stage(window.bucket_seconds), **accumulators}},
        {"$sort": {"_id": 1}},
        {"$limit": 2000},
    ]
    rows = [row async for row in database[Collections.METRICS_GLOBAL].aggregate(pipeline)]

    output: Dict[str, List[Dict[str, Any]]] = {metric: [] for metric in metrics}
    for row in rows:
        stamp = jsonable(row["_id"])
        for metric in metrics:
            spec = METRIC_SPECS.get(metric)
            if not spec:
                continue
            if spec["op"] == "sum":
                value = row.get(f"m_{metric}", 0) or 0
            elif spec["op"] == "rate":
                value = (row.get(f"m_{metric}", 0) or 0) / max(window.bucket_seconds, 1)
            elif spec["op"] == "avg":
                value = row.get(f"m_{metric}") or 0
            else:
                numerator = sum(row.get(f"n_{field}", 0) or 0 for field in spec["numerator"])
                denominator = row.get(f"d_{spec['denominator']}", 0) or 0
                value = numerator / denominator if denominator else 0.0
            output[metric].append({"t": stamp, "v": round(float(value), 4)})

    # The newest bucket is still filling; plotting it dips every chart.
    return {
        metric: drop_open_bucket(points, window.bucket_seconds) for metric, points in output.items()
    }


async def _totals(database, start: datetime, end: datetime) -> Dict[str, Any]:
    pipeline = [
        {"$match": {"window_start": {"$gte": start, "$lte": end}}},
        {
            "$group": {
                "_id": None,
                "requests": {"$sum": "$requests"},
                "errors_4xx": {"$sum": "$errors_4xx"},
                "errors_5xx": {"$sum": "$errors_5xx"},
                "bytes_sent": {"$sum": "$bytes_sent"},
                "p95": {"$avg": "$p95_response_time"},
                "p99": {"$avg": "$p99_response_time"},
                "p50": {"$avg": "$p50_response_time"},
                "avg_rt": {"$avg": "$avg_response_time"},
                "apdex": {"$avg": "$apdex"},
                "unique_ips": {"$avg": "$unique_ips"},
                "unique_sessions": {"$avg": "$unique_sessions"},
                "bot_ratio": {"$avg": "$bot_ratio"},
                "cache_hit_rate": {"$avg": "$cache_hit_rate"},
                "windows": {"$sum": 1},
                "peak_rps": {"$max": "$rps"},
            }
        },
    ]
    rows = [row async for row in database[Collections.METRICS_GLOBAL].aggregate(pipeline)]
    if not rows:
        return {"requests": 0, "windows": 0}
    row = rows[0]
    requests = row.get("requests", 0) or 0
    errors = (row.get("errors_4xx", 0) or 0) + (row.get("errors_5xx", 0) or 0)
    seconds = max((end - start).total_seconds(), 1)
    return {
        "requests": requests,
        "rps": round(requests / seconds, 2),
        "peak_rps": round(row.get("peak_rps", 0) or 0, 2),
        "errors": errors,
        "errors_4xx": row.get("errors_4xx", 0) or 0,
        "errors_5xx": row.get("errors_5xx", 0) or 0,
        "error_rate": round(errors / requests, 5) if requests else 0.0,
        "server_error_rate": round((row.get("errors_5xx", 0) or 0) / requests, 5) if requests else 0.0,
        "availability": round(1 - (row.get("errors_5xx", 0) or 0) / requests, 5) if requests else 1.0,
        "bytes_sent": row.get("bytes_sent", 0) or 0,
        "p50_response_time": round(row.get("p50") or 0, 1),
        "p95_response_time": round(row.get("p95") or 0, 1),
        "p99_response_time": round(row.get("p99") or 0, 1),
        "avg_response_time": round(row.get("avg_rt") or 0, 1),
        "apdex": round(row.get("apdex") or 1, 4),
        "unique_ips": round(row.get("unique_ips") or 0),
        "unique_sessions": round(row.get("unique_sessions") or 0),
        "bot_ratio": round(row.get("bot_ratio") or 0, 4),
        "cache_hit_rate": round(row.get("cache_hit_rate") or 0, 4),
        "windows": row.get("windows", 0),
    }


def _delta(current: float, previous: float) -> Optional[float]:
    if previous in (0, None):
        return None
    return round((current - previous) / abs(previous) * 100.0, 2)


@router.get("/overview", summary="Headline KPIs with period-over-period deltas")
async def overview(window: TimeRange = Depends(time_range)):
    database = db()
    span = window.end - window.start
    current = await _totals(database, window.start, window.end)
    previous = await _totals(database, window.start - span, window.start)

    series = await _series(
        database, window, ["rps", "error_rate", "p95_response_time", "unique_sessions"]
    )

    anomaly_pipeline = [
        {"$match": {"window_start": {"$gte": window.start, "$lte": window.end}}},
        {"$group": {"_id": "$severity", "count": {"$sum": 1}}},
    ]
    severities = {
        row["_id"]: row["count"]
        async for row in database[Collections.ANOMALIES].aggregate(anomaly_pipeline)
    }

    open_incidents = await database[Collections.INCIDENTS].count_documents({"status": "open"})
    firing_alerts = await database[Collections.ALERTS].count_documents(
        {"status": "firing", "created_at": {"$gte": datetime.now(timezone.utc) - timedelta(hours=1)}}
    )
    top_incidents = docs(
        [
            row
            async for row in database[Collections.INCIDENTS]
            .find({"status": {"$in": ["open", "acknowledged"]}})
            .sort([("severity", -1), ("last_seen_at", -1)])
            .limit(5)
        ]
    )

    return {
        "range": window.as_dict(),
        "totals": current,
        "deltas": {
            key: _delta(current.get(key, 0), previous.get(key, 0))
            for key in ("requests", "rps", "error_rate", "p95_response_time", "unique_sessions", "apdex")
        },
        "previous": previous,
        "series": series,
        "anomalies": {"by_severity": severities, "total": sum(severities.values())},
        "incidents": {"open": open_incidents, "top": top_incidents},
        "alerts": {"firing_last_hour": firing_alerts},
    }


@router.get("/metrics/timeseries", summary="Bucketed time series for one or more metrics")
async def timeseries(
    window: TimeRange = Depends(time_range),
    metrics: str = Query("requests,error_rate,p95_response_time", description="comma separated metric keys"),
    bucket_seconds: Optional[int] = Query(None, ge=10, le=86400),
):
    if bucket_seconds:
        window.bucket_seconds = bucket_seconds
    requested = [m.strip() for m in metrics.split(",") if m.strip() in METRIC_SPECS]
    if not requested:
        requested = ["requests"]
    series = await _series(db(), window, requested)
    return {
        "range": window.as_dict(),
        "metrics": {
            key: {
                "label": METRIC_SPECS[key]["label"],
                "unit": METRIC_SPECS[key]["unit"],
                "points": series.get(key, []),
            }
            for key in requested
        },
    }


@router.get("/metrics/catalog", summary="Metrics available to the chart builder")
async def metric_catalog():
    return {
        "metrics": [
            {"key": key, "label": spec["label"], "unit": spec["unit"], "aggregation": spec["op"]}
            for key, spec in METRIC_SPECS.items()
        ]
    }


@router.get("/metrics/status-breakdown", summary="Requests per status class over time")
async def status_breakdown(window: TimeRange = Depends(time_range)):
    pipeline = [
        {"$match": window.filter()},
        {
            "$group": {
                "_id": {"bucket": _bucket_stage(window.bucket_seconds), "class": "$status_class"},
                "requests": {"$sum": "$requests"},
            }
        },
        {"$sort": {"_id.bucket": 1}},
        {"$limit": 8000},
    ]
    buckets: Dict[str, Dict[str, Any]] = {}
    classes = set()
    async for row in db()[Collections.METRICS_STATUS].aggregate(pipeline):
        stamp = jsonable(row["_id"]["bucket"])
        status_class = row["_id"]["class"]
        classes.add(status_class)
        buckets.setdefault(stamp, {"t": stamp})[status_class] = row["requests"]

    ordered = sorted(classes)
    points = []
    for stamp in sorted(buckets):
        entry = buckets[stamp]
        for status_class in ordered:
            entry.setdefault(status_class, 0)
        points.append(entry)
    return {
        "range": window.as_dict(),
        "classes": ordered,
        "points": drop_open_bucket(points, window.bucket_seconds),
    }


@router.get("/metrics/baseline", summary="Seasonal baseline band for a metric")
async def baseline(metric: str = Query("requests")):
    document = await db()[Collections.BASELINES].find_one({"key": "seasonal"})
    if not document:
        return {"metric": metric, "available": False, "buckets": []}

    payload = document.get("value", {})
    now = datetime.now(timezone.utc)
    day_type = "weekend" if now.weekday() >= 5 else "weekday"
    entries = payload.get(day_type, {}) or payload.get("weekday", {})
    buckets = []
    for bucket, metrics in sorted(entries.items(), key=lambda kv: int(kv[0])):
        stats = metrics.get(metric)
        if not stats:
            continue
        minute = int(bucket) * document.get("bucket_minutes", 15)
        buckets.append(
            {
                "minute_of_day": minute,
                "time": f"{minute // 60:02d}:{minute % 60:02d}",
                "median": stats["median"],
                "p25": stats["p25"],
                "p75": stats["p75"],
                "p95": stats["p95"],
                "samples": stats["samples"],
            }
        )
    return {
        "metric": metric,
        "available": bool(buckets),
        "day_type": day_type,
        "bucket_minutes": document.get("bucket_minutes", 15),
        "updated_at": jsonable(document.get("updated_at")),
        "buckets": buckets,
    }


@router.get("/traffic/patterns", summary="Traffic profile clusters and a weekly heatmap")
async def traffic_patterns(window: TimeRange = Depends(time_range)):
    database = db()
    profiles_doc = await database[Collections.BASELINES].find_one({"key": "traffic_profiles"})

    pipeline = [
        {"$match": window.filter()},
        {
            "$group": {
                "_id": {
                    "dow": {"$dayOfWeek": "$window_start"},
                    "hour": {"$hour": "$window_start"},
                },
                "requests": {"$sum": "$requests"},
                "error_rate": {"$avg": "$error_rate"},
                "p95": {"$avg": "$p95_response_time"},
                "windows": {"$sum": 1},
            }
        },
    ]
    heatmap = []
    async for row in database[Collections.METRICS_GLOBAL].aggregate(pipeline):
        heatmap.append(
            {
                # Mongo's $dayOfWeek is 1=Sunday; normalise to 0=Monday.
                "day": (row["_id"]["dow"] + 5) % 7,
                "hour": row["_id"]["hour"],
                "requests": row["requests"],
                "rps": round(row["requests"] / max(row["windows"] * 60, 1), 2),
                "error_rate": round(row.get("error_rate") or 0, 5),
                "p95_response_time": round(row.get("p95") or 0, 1),
                "windows": row["windows"],
            }
        )

    return {
        "range": window.as_dict(),
        "profiles": jsonable(profiles_doc.get("value", [])) if profiles_doc else [],
        "profiles_updated_at": jsonable(profiles_doc.get("updated_at")) if profiles_doc else None,
        "heatmap": sorted(heatmap, key=lambda row: (row["day"], row["hour"])),
    }
