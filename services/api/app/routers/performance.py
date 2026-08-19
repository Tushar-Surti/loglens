"""Endpoint, service and session analytics."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from loglens_common.schemas import Collections

from ..deps import Pagination, TimeRange, db, pagination, time_range
from ..serialization import doc, docs, jsonable

router = APIRouter(tags=["performance"])

ENDPOINT_SORTS = {
    "requests": "requests",
    "error_rate": "error_rate",
    "p95": "p95_response_time",
    "p99": "p99_response_time",
    "apdex": "apdex",
    "latency_budget": "latency_budget",
}


@router.get("/endpoints", summary="Endpoint performance table")
async def list_endpoints(
    window: TimeRange = Depends(time_range),
    limit: int = Query(50, ge=1, le=300),
    sort: str = Query("requests", pattern="^(requests|error_rate|p95|p99|apdex|latency_budget)$"),
    service: Optional[str] = None,
    search: Optional[str] = None,
    with_sparklines: bool = Query(True),
):
    database = db()
    match: Dict[str, Any] = {"window_start": {"$gte": window.start, "$lte": window.end}}
    if service:
        match["service"] = service
    if search:
        match["endpoint"] = {"$regex": search, "$options": "i"}

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": {"endpoint": "$endpoint", "method": "$method"},
                "service": {"$last": "$service"},
                "requests": {"$sum": "$requests"},
                "errors_4xx": {"$sum": "$errors_4xx"},
                "errors_5xx": {"$sum": "$errors_5xx"},
                "bytes_sent": {"$sum": "$bytes_sent"},
                # Latency percentiles are averaged across windows: exact global
                # percentiles would need the digests, which we deliberately do
                # not persist.  Documented as an approximation in the UI.
                "p50_response_time": {"$avg": "$p50_response_time"},
                "p95_response_time": {"$avg": "$p95_response_time"},
                "p99_response_time": {"$avg": "$p99_response_time"},
                "max_response_time": {"$max": "$max_response_time"},
                "avg_response_time": {"$avg": "$avg_response_time"},
                "apdex": {"$avg": "$apdex"},
                "unique_ips": {"$avg": "$unique_ips"},
                "cache_hit_rate": {"$avg": "$cache_hit_rate"},
                "windows": {"$sum": 1},
            }
        },
        {"$limit": 500},
    ]
    rows = [row async for row in database[Collections.METRICS_ENDPOINT].aggregate(pipeline)]

    total_requests = sum(row.get("requests", 0) for row in rows) or 1
    items: List[Dict[str, Any]] = []
    for row in rows:
        requests = row.get("requests", 0) or 0
        errors = (row.get("errors_4xx", 0) or 0) + (row.get("errors_5xx", 0) or 0)
        p95 = round(row.get("p95_response_time") or 0, 1)
        items.append(
            {
                "endpoint": row["_id"]["endpoint"],
                "method": row["_id"]["method"],
                "service": row.get("service"),
                "requests": requests,
                "traffic_share": round(requests / total_requests, 4),
                "errors_4xx": row.get("errors_4xx", 0) or 0,
                "errors_5xx": row.get("errors_5xx", 0) or 0,
                "error_rate": round(errors / max(requests, 1), 5),
                "p50_response_time": round(row.get("p50_response_time") or 0, 1),
                "p95_response_time": p95,
                "p99_response_time": round(row.get("p99_response_time") or 0, 1),
                "max_response_time": round(row.get("max_response_time") or 0, 1),
                "avg_response_time": round(row.get("avg_response_time") or 0, 1),
                "apdex": round(row.get("apdex") or 1, 4),
                "unique_ips": round(row.get("unique_ips") or 0),
                "cache_hit_rate": round(row.get("cache_hit_rate") or 0, 4),
                "bytes_sent": row.get("bytes_sent", 0) or 0,
                "windows": row.get("windows", 0),
                # How much of a 500 ms budget this endpoint consumes.
                "latency_budget": round(p95 / 500.0, 3),
            }
        )

    reverse = sort != "apdex"
    items.sort(key=lambda item: item.get(ENDPOINT_SORTS[sort], 0) or 0, reverse=reverse)
    items = items[:limit]

    if with_sparklines and items:
        keys = [item["endpoint"] for item in items]
        spark_pipeline = [
            {"$match": {**match, "endpoint": {"$in": keys}}},
            {
                "$group": {
                    "_id": {
                        "endpoint": "$endpoint",
                        "bucket": {"$dateTrunc": {"date": "$window_start", "unit": "second",
                                                  "binSize": max(window.bucket_seconds, 60)}},
                    },
                    "requests": {"$sum": "$requests"},
                    "p95": {"$avg": "$p95_response_time"},
                    "error_rate": {"$avg": "$error_rate"},
                }
            },
            {"$sort": {"_id.bucket": 1}},
            {"$limit": 6000},
        ]
        sparks: Dict[str, List[Dict[str, Any]]] = {}
        async for row in database[Collections.METRICS_ENDPOINT].aggregate(spark_pipeline):
            sparks.setdefault(row["_id"]["endpoint"], []).append(
                {
                    "t": jsonable(row["_id"]["bucket"]),
                    "requests": row["requests"],
                    "p95": round(row.get("p95") or 0, 1),
                    "error_rate": round(row.get("error_rate") or 0, 5),
                }
            )
        for item in items:
            item["sparkline"] = sparks.get(item["endpoint"], [])

    return {"range": window.as_dict(), "items": items, "count": len(items)}


@router.get("/endpoints/detail", summary="One endpoint: series, anomalies and slowest requests")
async def endpoint_detail(
    window: TimeRange = Depends(time_range),
    endpoint: str = Query(..., description="normalised route, e.g. /api/v1/checkout"),
    method: Optional[str] = None,
):
    database = db()
    criteria: Dict[str, Any] = {"endpoint": endpoint, "window_start": {"$gte": window.start, "$lte": window.end}}
    if method:
        criteria["method"] = method.upper()

    series = database[Collections.METRICS_ENDPOINT].find(criteria, {"_id": 0}).sort("window_start", 1).limit(3000)
    windows = [row async for row in series]
    if not windows:
        raise HTTPException(status_code=404, detail="no metrics for this endpoint in range")

    anomalies = (
        database[Collections.ANOMALIES]
        .find({"entity_type": "endpoint", "entity": endpoint}, {"_id": 0})
        .sort("window_start", -1)
        .limit(60)
    )
    slowest = (
        database[Collections.RAW_LOGS]
        .find({"endpoint": endpoint, "timestamp": {"$gte": window.start, "$lte": window.end}}, {"_id": 0})
        .sort("response_time_ms", -1)
        .limit(20)
    )
    errors = (
        database[Collections.RAW_LOGS]
        .find({"endpoint": endpoint, "status": {"$gte": 400},
               "timestamp": {"$gte": window.start, "$lte": window.end}}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(20)
    )

    requests = sum(row.get("requests", 0) for row in windows)
    errors_total = sum((row.get("errors_4xx", 0) or 0) + (row.get("errors_5xx", 0) or 0) for row in windows)
    return {
        "endpoint": endpoint,
        "method": method,
        "range": window.as_dict(),
        "summary": {
            "requests": requests,
            "error_rate": round(errors_total / max(requests, 1), 5),
            "p95_response_time": round(sum(r.get("p95_response_time", 0) for r in windows) / max(len(windows), 1), 1),
            "p99_response_time": round(sum(r.get("p99_response_time", 0) for r in windows) / max(len(windows), 1), 1),
            "apdex": round(sum(r.get("apdex", 1) for r in windows) / max(len(windows), 1), 4),
            "windows": len(windows),
            "service": windows[-1].get("service"),
        },
        "series": docs(windows),
        "anomalies": docs([row async for row in anomalies]),
        "slowest_requests": docs([row async for row in slowest]),
        "recent_errors": docs([row async for row in errors]),
    }


@router.get("/services", summary="Per-service health rollup")
async def list_services(window: TimeRange = Depends(time_range)):
    pipeline = [
        {"$match": {"window_start": {"$gte": window.start, "$lte": window.end}}},
        {
            "$group": {
                "_id": "$service",
                "requests": {"$sum": "$requests"},
                "error_rate": {"$avg": "$error_rate"},
                "server_error_rate": {"$avg": "$server_error_rate"},
                "p95_response_time": {"$avg": "$p95_response_time"},
                "p99_response_time": {"$avg": "$p99_response_time"},
                "apdex": {"$avg": "$apdex"},
                "hosts": {"$max": "$hosts"},
                "windows": {"$sum": 1},
            }
        },
        {"$sort": {"requests": -1}},
    ]
    items = [
        {
            "service": row["_id"],
            "requests": row["requests"],
            "error_rate": round(row.get("error_rate") or 0, 5),
            "server_error_rate": round(row.get("server_error_rate") or 0, 5),
            "p95_response_time": round(row.get("p95_response_time") or 0, 1),
            "p99_response_time": round(row.get("p99_response_time") or 0, 1),
            "apdex": round(row.get("apdex") or 1, 4),
            "hosts": row.get("hosts", 0),
            "windows": row.get("windows", 0),
            "status": "critical"
            if (row.get("server_error_rate") or 0) > 0.05
            else ("degraded" if (row.get("error_rate") or 0) > 0.08 or (row.get("apdex") or 1) < 0.85 else "healthy"),
        }
        async for row in db()[Collections.METRICS_SERVICE].aggregate(pipeline)
    ]
    return {"range": window.as_dict(), "items": items}


@router.get("/sessions", summary="User sessions with behaviour analytics")
async def list_sessions(
    window: TimeRange = Depends(time_range),
    page: Pagination = Depends(pagination),
    min_requests: int = Query(1, ge=1),
    bots: Optional[bool] = Query(None, description="filter to bot or human sessions"),
    converted: Optional[bool] = None,
    sort: str = Query("session_start", pattern="^(session_start|requests|duration_seconds|requests_per_minute)$"),
):
    database = db()
    criteria: Dict[str, Any] = {
        "session_start": {"$gte": window.start, "$lte": window.end},
        "requests": {"$gte": min_requests},
    }
    if bots is not None:
        criteria["is_bot"] = bots
    if converted is not None:
        criteria["converted"] = converted

    cursor = (
        database[Collections.SESSIONS].find(criteria, {"_id": 0}).sort(sort, -1)
        .skip(page.offset).limit(page.limit)
    )
    items = [row async for row in cursor]

    summary_rows = [
        row
        async for row in database[Collections.SESSIONS].aggregate(
            [
                {"$match": criteria},
                {
                    "$group": {
                        "_id": None,
                        "sessions": {"$sum": 1},
                        "requests": {"$sum": "$requests"},
                        "avg_duration": {"$avg": "$duration_seconds"},
                        "avg_requests": {"$avg": "$requests"},
                        "avg_endpoints": {"$avg": "$unique_endpoints"},
                        "converted": {"$sum": {"$cond": ["$converted", 1, 0]}},
                        "bots": {"$sum": {"$cond": ["$is_bot", 1, 0]}},
                        "errors": {"$sum": "$error_count"},
                    }
                },
            ]
        )
    ]
    summary = summary_rows[0] if summary_rows else {}
    sessions_total = summary.get("sessions", 0) or 0

    entry_points = [
        {"endpoint": row["_id"], "sessions": row["count"]}
        async for row in database[Collections.SESSIONS].aggregate(
            [{"$match": criteria}, {"$sortByCount": "$entry_endpoint"}, {"$limit": 10}]
        )
    ]
    exit_points = [
        {"endpoint": row["_id"], "sessions": row["count"]}
        async for row in database[Collections.SESSIONS].aggregate(
            [{"$match": criteria}, {"$sortByCount": "$exit_endpoint"}, {"$limit": 10}]
        )
    ]

    return {
        "range": window.as_dict(),
        "items": docs(items),
        "limit": page.limit,
        "offset": page.offset,
        "summary": {
            "sessions": sessions_total,
            "requests": summary.get("requests", 0),
            "avg_duration_seconds": round(summary.get("avg_duration") or 0, 1),
            "avg_requests_per_session": round(summary.get("avg_requests") or 0, 2),
            "avg_endpoints_per_session": round(summary.get("avg_endpoints") or 0, 2),
            "conversion_rate": round((summary.get("converted", 0) or 0) / max(sessions_total, 1), 4),
            "bot_share": round((summary.get("bots", 0) or 0) / max(sessions_total, 1), 4),
            "errors": summary.get("errors", 0),
        },
        "entry_points": entry_points,
        "exit_points": exit_points,
    }


@router.get("/sessions/{session_id}", summary="One session with its request trail")
async def get_session(session_id: str):
    database = db()
    session = await database[Collections.SESSIONS].find_one({"session_id": session_id}, {"_id": 0})
    events = (
        database[Collections.RAW_LOGS].find({"session_id": session_id}, {"_id": 0}).sort("timestamp", 1).limit(500)
    )
    trail = [row async for row in events]
    if not session and not trail:
        raise HTTPException(status_code=404, detail="session not found")
    anomalies = (
        database[Collections.ANOMALIES]
        .find({"entity_type": "session", "entity": session_id}, {"_id": 0})
        .limit(20)
    )
    return {
        "session": doc(session),
        "events": docs(trail),
        "anomalies": docs([row async for row in anomalies]),
    }
