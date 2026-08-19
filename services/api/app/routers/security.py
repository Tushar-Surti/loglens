"""Anomalies, incidents, alerts and source-IP intelligence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from loglens_common.schemas import AnomalyType, Collections, Severity

from ..deps import Pagination, TimeRange, db, pagination, time_range
from ..serialization import doc, docs, jsonable

router = APIRouter(tags=["security"])


def _severity_filter(minimum: Optional[str]) -> Optional[Dict[str, Any]]:
    if not minimum:
        return None
    rank = Severity.ORDER.get(minimum)
    if rank is None:
        return None
    return {"severity": {"$in": [name for name, order in Severity.ORDER.items() if order >= rank]}}


# ── Anomalies ────────────────────────────────────────────────────────────────
@router.get("/anomalies", summary="Anomaly feed")
async def list_anomalies(
    window: TimeRange = Depends(time_range),
    page: Pagination = Depends(pagination),
    severity: Optional[str] = Query(None, description="minimum severity"),
    type: Optional[str] = Query(None, description="comma separated anomaly types"),
    entity_type: Optional[str] = None,
    entity: Optional[str] = None,
    status: Optional[str] = None,
    min_score: Optional[float] = Query(None, ge=0, le=100),
    detector: Optional[str] = None,
    sort: str = Query("detected_at", pattern="^(detected_at|score|severity|window_start)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    conditions: List[Dict[str, Any]] = [{"window_start": {"$gte": window.start, "$lte": window.end}}]
    severity_clause = _severity_filter(severity)
    if severity_clause:
        conditions.append(severity_clause)
    if type:
        conditions.append({"type": {"$in": [t.strip() for t in type.split(",")]}})
    if entity_type:
        conditions.append({"entity_type": entity_type})
    if entity:
        conditions.append({"entity": entity})
    if status:
        conditions.append({"status": {"$in": [s.strip() for s in status.split(",")]}})
    if min_score is not None:
        conditions.append({"score": {"$gte": min_score}})
    if detector:
        conditions.append({"$or": [{"detector": detector}, {"detectors": detector}]})

    criteria = {"$and": conditions}
    database = db()
    cursor = (
        database[Collections.ANOMALIES]
        .find(criteria, {"_id": 0})
        .sort([(sort, -1 if order == "desc" else 1)])
        .skip(page.offset)
        .limit(page.limit)
    )
    items = [row async for row in cursor]
    total = await database[Collections.ANOMALIES].count_documents(criteria, maxTimeMS=4000)
    return {
        "range": window.as_dict(),
        "items": docs(items),
        "count": len(items),
        "total": total,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.get("/anomalies/summary", summary="Anomaly counts by type, severity and time")
async def anomalies_summary(window: TimeRange = Depends(time_range)):
    database = db()
    match = {"window_start": {"$gte": window.start, "$lte": window.end}}

    by_type = [
        {"type": row["_id"], "label": AnomalyType.LABELS.get(row["_id"], row["_id"]),
         "count": row["count"], "max_score": round(row["max_score"], 1), "avg_score": round(row["avg_score"], 1)}
        async for row in database[Collections.ANOMALIES].aggregate(
            [
                {"$match": match},
                {"$group": {"_id": "$type", "count": {"$sum": 1},
                            "max_score": {"$max": "$score"}, "avg_score": {"$avg": "$score"}}},
                {"$sort": {"count": -1}},
            ]
        )
    ]

    by_severity = {
        row["_id"]: row["count"]
        async for row in database[Collections.ANOMALIES].aggregate(
            [{"$match": match}, {"$group": {"_id": "$severity", "count": {"$sum": 1}}}]
        )
    }

    timeline_rows = [
        row
        async for row in database[Collections.ANOMALIES].aggregate(
            [
                {"$match": match},
                {
                    "$group": {
                        "_id": {
                            "bucket": {"$dateTrunc": {"date": "$window_start", "unit": "second",
                                                      "binSize": window.bucket_seconds}},
                            "severity": "$severity",
                        },
                        "count": {"$sum": 1},
                    }
                },
                {"$sort": {"_id.bucket": 1}},
                {"$limit": 4000},
            ]
        )
    ]
    timeline: Dict[str, Dict[str, Any]] = {}
    for row in timeline_rows:
        stamp = jsonable(row["_id"]["bucket"])
        entry = timeline.setdefault(stamp, {"t": stamp, "info": 0, "low": 0, "medium": 0, "high": 0, "critical": 0})
        entry[row["_id"]["severity"]] = row["count"]

    top_entities = [
        {"entity": row["_id"]["entity"], "entity_type": row["_id"]["entity_type"],
         "count": row["count"], "max_score": round(row["max_score"], 1)}
        async for row in database[Collections.ANOMALIES].aggregate(
            [
                {"$match": match},
                {"$group": {"_id": {"entity": "$entity", "entity_type": "$entity_type"},
                            "count": {"$sum": 1}, "max_score": {"$max": "$score"}}},
                {"$sort": {"count": -1}},
                {"$limit": 12},
            ]
        )
    ]

    quality = await database[Collections.CONFIG].find_one({"key": "detection_quality"})
    return {
        "range": window.as_dict(),
        "by_type": by_type,
        "by_severity": by_severity,
        "timeline": [timeline[key] for key in sorted(timeline)],
        "top_entities": top_entities,
        "detection_quality": jsonable(quality.get("value")) if quality else None,
    }


@router.get("/anomalies/{anomaly_id}", summary="Anomaly detail with evidence")
async def get_anomaly(anomaly_id: str):
    database = db()
    record = await database[Collections.ANOMALIES].find_one({"anomaly_id": anomaly_id}, {"_id": 0})
    if not record:
        raise HTTPException(status_code=404, detail="anomaly not found")

    payload: Dict[str, Any] = {"anomaly": doc(record)}
    if record.get("incident_id"):
        payload["incident"] = doc(
            await database[Collections.INCIDENTS].find_one({"incident_id": record["incident_id"]}, {"_id": 0})
        )

    # Surrounding metric windows give the reviewer the "before and after".
    collection = {
        "global": Collections.METRICS_GLOBAL,
        "endpoint": Collections.METRICS_ENDPOINT,
        "ip": Collections.METRICS_IP,
        "country": Collections.METRICS_GEO,
    }.get(record.get("entity_type"), Collections.METRICS_GLOBAL)
    criteria: Dict[str, Any] = {}
    if record["entity_type"] == "endpoint":
        criteria["endpoint"] = record["entity"]
    elif record["entity_type"] == "ip":
        criteria["ip"] = record["entity"]
    elif record["entity_type"] == "country":
        criteria["country"] = record["entity"]

    context = (
        database[collection]
        .find({**criteria}, {"_id": 0})
        .sort("window_start", -1)
        .limit(90)
    )
    payload["context"] = docs(list(reversed([row async for row in context])))

    if record["entity_type"] == "ip":
        samples = (
            database[Collections.RAW_LOGS]
            .find({"ip": record["entity"]}, {"_id": 0})
            .sort("timestamp", -1)
            .limit(25)
        )
        payload["sample_logs"] = docs([row async for row in samples])
    return payload


@router.patch("/anomalies/{anomaly_id}", summary="Acknowledge, resolve or suppress an anomaly")
async def update_anomaly(anomaly_id: str, payload: Dict[str, Any] = Body(...)):
    allowed = {"open", "acknowledged", "resolved", "suppressed"}
    status = payload.get("status")
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(allowed)}")
    update = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if payload.get("note"):
        update["note"] = str(payload["note"])[:2000]
    result = await db()[Collections.ANOMALIES].update_one({"anomaly_id": anomaly_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="anomaly not found")
    return {"anomaly_id": anomaly_id, **jsonable(update)}


# ── Incidents ────────────────────────────────────────────────────────────────
@router.get("/incidents", summary="Correlated incidents")
async def list_incidents(
    window: TimeRange = Depends(time_range),
    page: Pagination = Depends(pagination),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    family: Optional[str] = None,
    include_all_time_open: bool = Query(True, description="always include still-open incidents"),
):
    conditions: List[Dict[str, Any]] = []
    time_clause: Dict[str, Any] = {"started_at": {"$gte": window.start, "$lte": window.end}}
    if include_all_time_open:
        conditions.append({"$or": [time_clause, {"status": {"$in": ["open", "acknowledged"]}}]})
    else:
        conditions.append(time_clause)
    if status:
        conditions.append({"status": {"$in": [s.strip() for s in status.split(",")]}})
    severity_clause = _severity_filter(severity)
    if severity_clause:
        conditions.append(severity_clause)
    if family:
        conditions.append({"family": family})

    criteria = {"$and": conditions}
    database = db()
    cursor = (
        database[Collections.INCIDENTS]
        .find(criteria, {"_id": 0, "timeline": {"$slice": -12}})
        .sort([("last_seen_at", -1)])
        .skip(page.offset)
        .limit(page.limit)
    )
    items = [row async for row in cursor]
    total = await database[Collections.INCIDENTS].count_documents(criteria, maxTimeMS=4000)
    counts = {
        row["_id"]: row["count"]
        async for row in database[Collections.INCIDENTS].aggregate(
            [{"$match": criteria}, {"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        )
    }
    return {
        "range": window.as_dict(),
        "items": docs(items),
        "total": total,
        "by_status": counts,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.get("/incidents/{incident_id}", summary="Incident detail with its anomalies")
async def get_incident(incident_id: str):
    database = db()
    incident = await database[Collections.INCIDENTS].find_one({"incident_id": incident_id}, {"_id": 0})
    if not incident:
        raise HTTPException(status_code=404, detail="incident not found")

    anomalies = (
        database[Collections.ANOMALIES]
        .find({"incident_id": incident_id}, {"_id": 0})
        .sort("window_start", 1)
        .limit(500)
    )
    alerts = database[Collections.ALERTS].find({"incident_id": incident_id}, {"_id": 0}).limit(50)

    started = incident.get("started_at")
    ended = incident.get("last_seen_at")
    context = (
        database[Collections.METRICS_GLOBAL]
        .find({"window_start": {"$gte": started, "$lte": ended}}, {"_id": 0})
        .sort("window_start", 1)
        .limit(600)
    )
    return {
        "incident": doc(incident),
        "anomalies": docs([row async for row in anomalies]),
        "alerts": docs([row async for row in alerts]),
        "metrics": docs([row async for row in context]),
    }


@router.patch("/incidents/{incident_id}", summary="Acknowledge or resolve an incident")
async def update_incident(incident_id: str, payload: Dict[str, Any] = Body(...)):
    allowed = {"open", "acknowledged", "resolved"}
    status = payload.get("status")
    if status not in allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(allowed)}")
    update: Dict[str, Any] = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if status == "resolved":
        update["resolved_at"] = datetime.now(timezone.utc)
        update["resolution"] = payload.get("resolution", "manual")
    if payload.get("acknowledged_by"):
        update["acknowledged_by"] = str(payload["acknowledged_by"])[:120]
    if payload.get("note"):
        update["note"] = str(payload["note"])[:4000]
    result = await db()[Collections.INCIDENTS].update_one({"incident_id": incident_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="incident not found")
    return {"incident_id": incident_id, **jsonable(update)}


# ── Alerts ───────────────────────────────────────────────────────────────────
@router.get("/alerts", summary="Fired alerts")
async def list_alerts(
    window: TimeRange = Depends(time_range),
    page: Pagination = Depends(pagination),
    severity: Optional[str] = None,
    status: Optional[str] = None,
):
    conditions: List[Dict[str, Any]] = [{"created_at": {"$gte": window.start, "$lte": window.end}}]
    severity_clause = _severity_filter(severity)
    if severity_clause:
        conditions.append(severity_clause)
    if status:
        conditions.append({"status": status})
    criteria = {"$and": conditions}
    database = db()
    cursor = (
        database[Collections.ALERTS].find(criteria, {"_id": 0}).sort("created_at", -1)
        .skip(page.offset).limit(page.limit)
    )
    items = [row async for row in cursor]
    return {
        "range": window.as_dict(),
        "items": docs(items),
        "total": await database[Collections.ALERTS].count_documents(criteria, maxTimeMS=4000),
        "limit": page.limit,
        "offset": page.offset,
    }


@router.patch("/alerts/{alert_id}", summary="Acknowledge or close an alert")
async def update_alert(alert_id: str, payload: Dict[str, Any] = Body(...)):
    update: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    if "acknowledged" in payload:
        update["acknowledged"] = bool(payload["acknowledged"])
    if payload.get("status") in {"firing", "resolved", "silenced"}:
        update["status"] = payload["status"]
    result = await db()[Collections.ALERTS].update_one({"alert_id": alert_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="alert not found")
    return {"alert_id": alert_id, **jsonable(update)}


# ── Source IPs ───────────────────────────────────────────────────────────────
@router.get("/ips", summary="Top and suspicious source IPs")
async def list_ips(
    window: TimeRange = Depends(time_range),
    limit: int = Query(50, ge=1, le=500),
    sort: str = Query("requests", pattern="^(requests|error_ratio|unique_paths|auth_fail_count|threat_score)$"),
    suspicious_only: bool = False,
    country: Optional[str] = None,
):
    database = db()
    match: Dict[str, Any] = {"window_start": {"$gte": window.start, "$lte": window.end}}
    if country:
        match["country"] = country.upper()

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": "$ip",
                "requests": {"$sum": "$requests"},
                "windows": {"$sum": 1},
                "errors": {"$sum": {"$multiply": ["$requests", "$error_ratio"]}},
                "not_found": {"$sum": {"$multiply": ["$requests", "$not_found_ratio"]}},
                "auth_fail_count": {"$sum": "$auth_fail_count"},
                "unique_paths": {"$max": "$unique_paths"},
                "unique_user_agents": {"$max": "$unique_user_agents"},
                "bytes_sent": {"$sum": "$bytes_sent"},
                "peak_rps": {"$max": "$rps"},
                "avg_response_time": {"$avg": "$avg_response_time"},
                "burstiness": {"$max": "$burstiness"},
                "path_entropy": {"$avg": "$path_entropy"},
                "bot_ratio": {"$avg": "$bot_ratio"},
                "country": {"$last": "$country"},
                "asn": {"$last": "$asn"},
                "org": {"$last": "$org"},
                "last_seen": {"$max": "$window_end"},
                "first_seen": {"$min": "$window_start"},
                "attack_label": {"$last": "$dominant_attack_label"},
            }
        },
        {"$sort": {"requests": -1}},
        {"$limit": 800},
    ]
    rows = [row async for row in database[Collections.METRICS_IP].aggregate(pipeline)]

    # Attach the strongest anomaly per IP so the table can rank by real threat.
    ips = [row["_id"] for row in rows]
    threat: Dict[str, Dict[str, Any]] = {}
    if ips:
        async for row in database[Collections.ANOMALIES].aggregate(
            [
                {"$match": {"entity_type": "ip", "entity": {"$in": ips},
                            "window_start": {"$gte": window.start, "$lte": window.end}}},
                {"$group": {"_id": "$entity", "score": {"$max": "$score"}, "count": {"$sum": 1},
                            "types": {"$addToSet": "$type"}, "severity": {"$last": "$severity"}}},
            ]
        ):
            threat[row["_id"]] = row

    items: List[Dict[str, Any]] = []
    for row in rows:
        ip = row["_id"]
        requests = row.get("requests", 0) or 0
        detection = threat.get(ip)
        items.append(
            {
                "ip": ip,
                "requests": requests,
                "windows": row.get("windows", 0),
                "error_ratio": round((row.get("errors", 0) or 0) / max(requests, 1), 4),
                "not_found_ratio": round((row.get("not_found", 0) or 0) / max(requests, 1), 4),
                "auth_fail_count": int(row.get("auth_fail_count", 0) or 0),
                "unique_paths": int(row.get("unique_paths", 0) or 0),
                "unique_user_agents": int(row.get("unique_user_agents", 0) or 0),
                "bytes_sent": int(row.get("bytes_sent", 0) or 0),
                "peak_rps": round(row.get("peak_rps", 0) or 0, 2),
                "avg_response_time": round(row.get("avg_response_time", 0) or 0, 1),
                "burstiness": round(row.get("burstiness", 0) or 0, 2),
                "path_entropy": round(row.get("path_entropy", 0) or 0, 3),
                "bot_ratio": round(row.get("bot_ratio", 0) or 0, 3),
                "country": row.get("country"),
                "asn": row.get("asn"),
                "org": row.get("org"),
                "first_seen": jsonable(row.get("first_seen")),
                "last_seen": jsonable(row.get("last_seen")),
                "threat_score": round(detection["score"], 1) if detection else 0.0,
                "severity": detection["severity"] if detection else None,
                "anomaly_count": detection["count"] if detection else 0,
                "anomaly_types": detection["types"] if detection else [],
                "ground_truth": row.get("attack_label"),
            }
        )

    if suspicious_only:
        items = [item for item in items if item["threat_score"] > 0]
    items.sort(key=lambda item: item.get(sort, 0) or 0, reverse=True)
    return {"range": window.as_dict(), "items": items[:limit], "count": min(len(items), limit)}


@router.get("/ips/{ip}", summary="Everything known about one source IP")
async def get_ip(ip: str, window: TimeRange = Depends(time_range)):
    database = db()
    series = (
        database[Collections.METRICS_IP]
        .find({"ip": ip, "window_start": {"$gte": window.start, "$lte": window.end}}, {"_id": 0})
        .sort("window_start", 1)
        .limit(1500)
    )
    windows = [row async for row in series]
    if not windows:
        recent = database[Collections.METRICS_IP].find({"ip": ip}, {"_id": 0}).sort("window_start", -1).limit(120)
        windows = list(reversed([row async for row in recent]))

    anomalies = (
        database[Collections.ANOMALIES]
        .find({"entity_type": "ip", "entity": ip}, {"_id": 0})
        .sort("window_start", -1)
        .limit(80)
    )
    logs = database[Collections.RAW_LOGS].find({"ip": ip}, {"_id": 0}).sort("timestamp", -1).limit(60)

    endpoints: Dict[str, int] = {}
    for entry in windows:
        for path in entry.get("sample_paths", []) or []:
            endpoints[path] = endpoints.get(path, 0) + 1

    total_requests = sum(entry.get("requests", 0) for entry in windows)
    return {
        "ip": ip,
        "range": window.as_dict(),
        "summary": {
            "requests": total_requests,
            "windows": len(windows),
            "peak_rps": round(max((entry.get("rps", 0) for entry in windows), default=0), 2),
            "country": windows[-1].get("country") if windows else None,
            "asn": windows[-1].get("asn") if windows else None,
            "org": windows[-1].get("org") if windows else None,
            "first_seen": jsonable(windows[0].get("window_start")) if windows else None,
            "last_seen": jsonable(windows[-1].get("window_end")) if windows else None,
            "bytes_sent": sum(entry.get("bytes_sent", 0) for entry in windows),
            "auth_fail_count": sum(entry.get("auth_fail_count", 0) for entry in windows),
        },
        "series": docs(windows),
        "top_endpoints": [{"endpoint": key, "windows": value} for key, value in
                          sorted(endpoints.items(), key=lambda kv: kv[1], reverse=True)[:15]],
        "anomalies": docs([row async for row in anomalies]),
        "recent_logs": docs([row async for row in logs]),
    }


@router.get("/security/summary", summary="Threat overview for the security page")
async def security_summary(window: TimeRange = Depends(time_range)):
    database = db()
    match = {"window_start": {"$gte": window.start, "$lte": window.end}, "entity_type": "ip"}

    attackers = [
        {
            "ip": row["_id"],
            "score": round(row["score"], 1),
            "anomalies": row["count"],
            "types": row["types"],
            "severity": row["severity"],
            "country": row.get("country"),
        }
        async for row in database[Collections.ANOMALIES].aggregate(
            [
                {"$match": match},
                {
                    "$group": {
                        "_id": "$entity",
                        "score": {"$max": "$score"},
                        "count": {"$sum": 1},
                        "types": {"$addToSet": "$type"},
                        "severity": {"$last": "$severity"},
                        "country": {"$last": "$metrics.country"},
                    }
                },
                {"$sort": {"score": -1}},
                {"$limit": 15},
            ]
        )
    ]

    by_type = {
        row["_id"]: row["count"]
        async for row in database[Collections.ANOMALIES].aggregate(
            [
                {"$match": {"window_start": {"$gte": window.start, "$lte": window.end},
                            "type": {"$in": [AnomalyType.DDOS_BURST, AnomalyType.ENDPOINT_SCAN,
                                             AnomalyType.AUTH_ABUSE, AnomalyType.DATA_SCRAPING,
                                             AnomalyType.SUSPICIOUS_IP, AnomalyType.BOT_SURGE,
                                             AnomalyType.GEO_ANOMALY]}}},
                {"$group": {"_id": "$type", "count": {"$sum": 1}}},
            ]
        )
    }

    blocked_candidates = await database[Collections.ANOMALIES].count_documents(
        {**match, "severity": {"$in": ["high", "critical"]}}
    )
    return {
        "range": window.as_dict(),
        "top_attackers": attackers,
        "attack_types": by_type,
        "high_severity_sources": blocked_candidates,
    }
