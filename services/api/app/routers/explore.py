"""Log Explorer: search, histogram, facets and single-event lookup."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from loglens_common.schemas import Collections

from .. import query as query_language
from ..deps import Pagination, TimeRange, db, drop_open_bucket, pagination, time_range
from ..serialization import doc, docs, jsonable

router = APIRouter(tags=["logs"])

#: Upper bound on the exact-count query; beyond this the total is reported as a
#: floor so a wide range cannot pin the database on a count nobody reads.
COUNT_CAP = 200_000

SORT_FIELDS = {
    "timestamp": "timestamp",
    "response_time": "response_time_ms",
    "status": "status",
    "bytes": "bytes_sent",
}


def _build_filter(
    window: TimeRange,
    q: Optional[str],
    status: Optional[str],
    method: Optional[str],
    endpoint: Optional[str],
    ip: Optional[str],
    service: Optional[str],
    country: Optional[str],
    session_id: Optional[str],
    min_response_time: Optional[float],
    only_errors: bool,
) -> Dict[str, Any]:
    parsed, _ = query_language.parse(q)
    conditions: List[Dict[str, Any]] = [{"timestamp": {"$gte": window.start, "$lte": window.end}}]
    if parsed:
        conditions.append(parsed)
    if status:
        codes = [int(code) for code in status.split(",") if code.strip().isdigit()]
        classes = [item.strip() for item in status.split(",") if item.strip().endswith("xx")]
        clause: List[Dict[str, Any]] = []
        if codes:
            clause.append({"status": {"$in": codes}})
        if classes:
            clause.append({"status_class": {"$in": classes}})
        if clause:
            conditions.append({"$or": clause} if len(clause) > 1 else clause[0])
    if method:
        conditions.append({"method": {"$in": [m.strip().upper() for m in method.split(",")]}})
    if endpoint:
        conditions.append({"endpoint": endpoint})
    if ip:
        conditions.append({"ip": ip})
    if service:
        conditions.append({"service": {"$in": [s.strip() for s in service.split(",")]}})
    if country:
        conditions.append({"country": {"$in": [c.strip().upper() for c in country.split(",")]}})
    if session_id:
        conditions.append({"session_id": session_id})
    if min_response_time is not None:
        conditions.append({"response_time_ms": {"$gte": min_response_time}})
    if only_errors:
        conditions.append({"status": {"$gte": 400}})
    return {"$and": conditions} if len(conditions) > 1 else conditions[0]


@router.get("/logs", summary="Search raw request logs")
async def search_logs(
    window: TimeRange = Depends(time_range),
    page: Pagination = Depends(pagination),
    q: Optional[str] = Query(None, description="query language, e.g. `status>=500 endpoint:/api/v1/*`"),
    status: Optional[str] = Query(None, description="comma separated codes and/or classes (500,4xx)"),
    method: Optional[str] = None,
    endpoint: Optional[str] = None,
    ip: Optional[str] = None,
    service: Optional[str] = None,
    country: Optional[str] = None,
    session_id: Optional[str] = None,
    min_response_time: Optional[float] = Query(None, ge=0),
    only_errors: bool = False,
    sort: str = Query("timestamp", pattern="^(timestamp|response_time|status|bytes)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    count_total: bool = Query(True, description="run an exact count (disable on very wide ranges)"),
):
    criteria = _build_filter(
        window, q, status, method, endpoint, ip, service, country, session_id, min_response_time, only_errors
    )
    database = db()
    direction = -1 if order == "desc" else 1

    cursor = (
        database[Collections.RAW_LOGS]
        .find(criteria, {"_id": 0})
        .sort([(SORT_FIELDS[sort], direction)])
        .skip(page.offset)
        .limit(page.limit)
    )
    items = [row async for row in cursor]

    total = None
    capped = False
    if count_total:
        # Bounded so a pathological range cannot pin the database. When the cap
        # is reached the number is a floor, not a count — say so, rather than
        # presenting "200,000" as if it were exact.
        total = await database[Collections.RAW_LOGS].count_documents(
            criteria, maxTimeMS=4000, limit=COUNT_CAP
        )
        capped = total >= COUNT_CAP

    return {
        "range": window.as_dict(),
        "query": q,
        "items": docs(items),
        "count": len(items),
        "total": total,
        "total_is_capped": capped,
        "limit": page.limit,
        "offset": page.offset,
        "has_more": len(items) == page.limit,
    }


@router.get("/logs/histogram", summary="Match counts over time for the current search")
async def logs_histogram(
    window: TimeRange = Depends(time_range),
    q: Optional[str] = None,
    status: Optional[str] = None,
    method: Optional[str] = None,
    endpoint: Optional[str] = None,
    ip: Optional[str] = None,
    service: Optional[str] = None,
    country: Optional[str] = None,
    session_id: Optional[str] = None,
    min_response_time: Optional[float] = None,
    only_errors: bool = False,
):
    criteria = _build_filter(
        window, q, status, method, endpoint, ip, service, country, session_id, min_response_time, only_errors
    )
    pipeline = [
        {"$match": criteria},
        {
            "$group": {
                "_id": {
                    "bucket": {
                        "$dateTrunc": {"date": "$timestamp", "unit": "second", "binSize": window.bucket_seconds}
                    },
                    "class": "$status_class",
                },
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"_id.bucket": 1}},
        {"$limit": 5000},
    ]
    buckets: Dict[str, Dict[str, Any]] = {}
    classes = set()
    async for row in db()[Collections.RAW_LOGS].aggregate(pipeline):
        stamp = jsonable(row["_id"]["bucket"])
        status_class = row["_id"].get("class") or "2xx"
        classes.add(status_class)
        entry = buckets.setdefault(stamp, {"t": stamp, "total": 0})
        entry[status_class] = row["count"]
        entry["total"] += row["count"]

    points = []
    for stamp in sorted(buckets):
        entry = buckets[stamp]
        for status_class in classes:
            entry.setdefault(status_class, 0)
        points.append(entry)
    return {
        "range": window.as_dict(),
        "classes": sorted(classes),
        "points": drop_open_bucket(points, window.bucket_seconds),
    }


@router.get("/logs/facets", summary="Top values per dimension for the current search")
async def logs_facets(
    window: TimeRange = Depends(time_range),
    q: Optional[str] = None,
    only_errors: bool = False,
    limit: int = Query(8, ge=1, le=25),
):
    criteria = _build_filter(window, q, None, None, None, None, None, None, None, None, only_errors)
    dimensions = ["status", "method", "endpoint", "service", "country", "device", "status_class"]
    facets = {
        dimension: [{"$sortByCount": f"${dimension}"}, {"$limit": limit}] for dimension in dimensions
    }
    pipeline = [{"$match": criteria}, {"$facet": facets}]
    result = [row async for row in db()[Collections.RAW_LOGS].aggregate(pipeline)]
    if not result:
        return {"facets": {}}
    return {
        "range": window.as_dict(),
        "facets": {
            dimension: [{"value": jsonable(entry["_id"]), "count": entry["count"]} for entry in result[0][dimension]]
            for dimension in dimensions
        },
    }


@router.get("/logs/query/describe", summary="Explain how a query string is parsed")
async def describe_query(q: Optional[str] = None):
    return jsonable(query_language.describe(q))


@router.get("/logs/{event_id}", summary="Single request, with its session context")
async def get_log(event_id: str, context: bool = Query(True, description="include neighbouring session requests")):
    database = db()
    record = await database[Collections.RAW_LOGS].find_one({"event_id": event_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="log event not found")

    payload: Dict[str, Any] = {"event": doc(record)}
    if context and record.get("session_id"):
        neighbours = (
            database[Collections.RAW_LOGS]
            .find({"session_id": record["session_id"]}, {"_id": 0})
            .sort("timestamp", 1)
            .limit(80)
        )
        payload["session_events"] = docs([row async for row in neighbours])
    if record.get("ip"):
        ip_windows = (
            database[Collections.METRICS_IP]
            .find({"ip": record["ip"]}, {"_id": 0})
            .sort("window_start", -1)
            .limit(20)
        )
        payload["ip_activity"] = docs([row async for row in ip_windows])
    return payload
