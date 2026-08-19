"""Geographic traffic analytics."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from loglens_common.schemas import Collections

from ..deps import TimeRange, db, time_range
from ..serialization import docs, jsonable

router = APIRouter(tags=["geo"])


@router.get("/geo", summary="Traffic by country with threat annotation")
async def geo_summary(
    window: TimeRange = Depends(time_range),
    limit: int = Query(60, ge=1, le=250),
):
    database = db()
    pipeline = [
        {"$match": {"window_start": {"$gte": window.start, "$lte": window.end}}},
        {
            "$group": {
                "_id": "$country",
                "country_name": {"$last": "$country_name"},
                "requests": {"$sum": "$requests"},
                "unique_ips": {"$max": "$unique_ips"},
                "unique_sessions": {"$max": "$unique_sessions"},
                "error_rate": {"$avg": "$error_rate"},
                "p95_response_time": {"$avg": "$p95_response_time"},
                "avg_response_time": {"$avg": "$avg_response_time"},
                "bytes_sent": {"$sum": "$bytes_sent"},
                "lat": {"$last": "$lat"},
                "lon": {"$last": "$lon"},
                "windows": {"$sum": 1},
                "attack_label": {"$last": "$dominant_attack_label"},
            }
        },
        {"$sort": {"requests": -1}},
        {"$limit": limit},
    ]
    rows = [row async for row in database[Collections.METRICS_GEO].aggregate(pipeline)]
    total = sum(row.get("requests", 0) for row in rows) or 1

    countries = [row["_id"] for row in rows]
    threat: Dict[str, Dict[str, Any]] = {}
    if countries:
        async for row in database[Collections.ANOMALIES].aggregate(
            [
                {
                    "$match": {
                        "entity_type": "country",
                        "entity": {"$in": countries},
                        "window_start": {"$gte": window.start, "$lte": window.end},
                    }
                },
                {"$group": {"_id": "$entity", "score": {"$max": "$score"}, "count": {"$sum": 1}}},
            ]
        ):
            threat[row["_id"]] = row

    items = [
        {
            "country": row["_id"],
            "country_name": row.get("country_name") or row["_id"],
            "requests": row.get("requests", 0),
            "share": round(row.get("requests", 0) / total, 4),
            "unique_ips": row.get("unique_ips", 0),
            "unique_sessions": row.get("unique_sessions", 0),
            "error_rate": round(row.get("error_rate") or 0, 5),
            "p95_response_time": round(row.get("p95_response_time") or 0, 1),
            "avg_response_time": round(row.get("avg_response_time") or 0, 1),
            "bytes_sent": row.get("bytes_sent", 0),
            "lat": round(row.get("lat") or 0, 4),
            "lon": round(row.get("lon") or 0, 4),
            "threat_score": round(threat.get(row["_id"], {}).get("score", 0), 1),
            "anomaly_count": threat.get(row["_id"], {}).get("count", 0),
            "ground_truth": row.get("attack_label"),
        }
        for row in rows
    ]
    return {"range": window.as_dict(), "items": items, "total_requests": total}


@router.get("/geo/timeseries", summary="Per-country traffic over time")
async def geo_timeseries(
    window: TimeRange = Depends(time_range),
    countries: Optional[str] = Query(None, description="comma separated ISO codes; defaults to the top 6"),
):
    database = db()
    selected: List[str]
    if countries:
        selected = [code.strip().upper() for code in countries.split(",") if code.strip()]
    else:
        top = database[Collections.METRICS_GEO].aggregate(
            [
                {"$match": {"window_start": {"$gte": window.start, "$lte": window.end}}},
                {"$group": {"_id": "$country", "requests": {"$sum": "$requests"}}},
                {"$sort": {"requests": -1}},
                {"$limit": 6},
            ]
        )
        selected = [row["_id"] async for row in top]

    pipeline = [
        {"$match": {"window_start": {"$gte": window.start, "$lte": window.end}, "country": {"$in": selected}}},
        {
            "$group": {
                "_id": {
                    "country": "$country",
                    "bucket": {"$dateTrunc": {"date": "$window_start", "unit": "second",
                                              "binSize": max(window.bucket_seconds, 300)}},
                },
                "requests": {"$sum": "$requests"},
            }
        },
        {"$sort": {"_id.bucket": 1}},
        {"$limit": 6000},
    ]
    buckets: Dict[str, Dict[str, Any]] = {}
    async for row in database[Collections.METRICS_GEO].aggregate(pipeline):
        stamp = jsonable(row["_id"]["bucket"])
        entry = buckets.setdefault(stamp, {"t": stamp})
        entry[row["_id"]["country"]] = row["requests"]

    points = []
    for stamp in sorted(buckets):
        entry = buckets[stamp]
        for code in selected:
            entry.setdefault(code, 0)
        points.append(entry)
    return {"range": window.as_dict(), "countries": selected, "points": points}


@router.get("/geo/points", summary="Point cloud for the globe view")
async def geo_points(window: TimeRange = Depends(time_range), limit: int = Query(300, ge=10, le=1500)):
    """Recent per-city activity, weighted for rendering on the 3D globe."""
    database = db()
    pipeline = [
        {"$match": {"timestamp": {"$gte": window.start, "$lte": window.end}}},
        {"$sample": {"size": 20000}},
        {
            "$group": {
                "_id": {"country": "$country", "city": "$city"},
                "requests": {"$sum": 1},
                "errors": {"$sum": {"$cond": [{"$gte": ["$status", 400]}, 1, 0]}},
                "lat": {"$last": "$lat"},
                "lon": {"$last": "$lon"},
                "country_name": {"$last": "$country_name"},
                "avg_response_time": {"$avg": "$response_time_ms"},
                "attack": {"$max": "$attack_label"},
            }
        },
        {"$sort": {"requests": -1}},
        {"$limit": limit},
    ]
    rows = [row async for row in database[Collections.RAW_LOGS].aggregate(pipeline)]
    peak = max((row.get("requests", 0) for row in rows), default=1)
    return {
        "range": window.as_dict(),
        "points": [
            {
                "country": row["_id"]["country"],
                "country_name": row.get("country_name"),
                "city": row["_id"]["city"],
                "lat": round(row.get("lat") or 0, 4),
                "lon": round(row.get("lon") or 0, 4),
                "requests": row.get("requests", 0),
                "weight": round((row.get("requests", 0) or 0) / max(peak, 1), 4),
                "error_rate": round((row.get("errors", 0) or 0) / max(row.get("requests", 1), 1), 4),
                "avg_response_time": round(row.get("avg_response_time") or 0, 1),
                "hostile": bool(row.get("attack")),
            }
            for row in rows
        ],
    }
