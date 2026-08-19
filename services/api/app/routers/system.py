"""Platform self-monitoring, runtime configuration and the demo control plane."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from loglens_common.config import settings
from loglens_common.schemas import Collections, DEFAULT_ALERT_RULES

from ..deps import TimeRange, db, time_range
from ..serialization import doc, docs, jsonable

router = APIRouter(tags=["system"])

THRESHOLD_BOUNDS: Dict[str, tuple] = {
    "zscore_threshold": (1.5, 10.0),
    "ewma_alpha": (0.01, 0.9),
    "error_rate_threshold": (0.001, 0.9),
    "latency_p95_multiplier": (1.1, 20.0),
    "ip_rps_threshold": (1.0, 5000.0),
    "scan_unique_paths": (3, 500),
    "auth_fail_threshold": (2, 500),
    "isoforest_contamination": (0.001, 0.3),
    "severity_critical": (50, 100),
    "severity_high": (30, 99),
    "severity_medium": (15, 95),
    "severity_low": (1, 90),
    "min_history_points": (3, 500),
}

THRESHOLD_META: Dict[str, Dict[str, str]] = {
    "zscore_threshold": {"label": "Outlier sensitivity (σ)",
                         "help": "How many robust standard deviations a window must sit from its baseline. Lower finds more."},
    "ewma_alpha": {"label": "EWMA smoothing",
                   "help": "Weight on the newest sample in the control chart. Higher reacts faster and is noisier."},
    "error_rate_threshold": {"label": "Error budget",
                             "help": "Error rate treated as unacceptable regardless of history."},
    "latency_p95_multiplier": {"label": "Latency budget (×baseline)",
                               "help": "p95 above this multiple of its baseline is a latency anomaly."},
    "ip_rps_threshold": {"label": "Per-IP request rate",
                         "help": "Requests per second from one source before it is considered volumetric abuse."},
    "scan_unique_paths": {"label": "Scan path count",
                          "help": "Distinct endpoints one IP may touch per minute before it looks like enumeration."},
    "auth_fail_threshold": {"label": "Auth failures",
                            "help": "Failed logins per minute per IP that indicate credential stuffing."},
    "isoforest_contamination": {"label": "Model contamination",
                                "help": "Assumed outlier fraction when training IsolationForest."},
    "severity_critical": {"label": "Critical at score", "help": "Fused score at or above this is critical."},
    "severity_high": {"label": "High at score", "help": "Fused score at or above this is high."},
    "severity_medium": {"label": "Medium at score", "help": "Fused score at or above this is medium."},
    "severity_low": {"label": "Reporting floor", "help": "Anomalies scoring below this are never stored."},
    "min_history_points": {"label": "Minimum history",
                           "help": "Windows of history a detector needs before it is allowed to fire."},
}


@router.get("/health", summary="Liveness probe")
async def health():
    try:
        await db().command("ping")
        database_ok = True
    except Exception:
        database_ok = False
    return {
        "status": "ok" if database_ok else "degraded",
        "service": "loglens-api",
        "environment": settings.environment,
        "mongodb": "up" if database_ok else "down",
        "time": jsonable(datetime.now(timezone.utc)),
    }


@router.get("/system/health", summary="Composite platform health")
async def system_health():
    database = db()
    snapshot = await database[Collections.SYSTEM_HEALTH].find_one(
        {"component": "platform"}, sort=[("ts", -1)]
    )
    components = {}
    for component in ("generator", "spark", "worker", "api"):
        latest = await database[Collections.SYSTEM_HEALTH].find_one({"component": component}, sort=[("ts", -1)])
        if latest:
            seen = latest["ts"]
            seen = seen.replace(tzinfo=timezone.utc) if seen.tzinfo is None else seen
            age = (datetime.now(timezone.utc) - seen).total_seconds()
            components[component] = {
                "status": "stale" if age > 90 else latest.get("status", "healthy"),
                "age_seconds": round(age, 1),
                "last_seen": jsonable(seen),
                "details": jsonable(latest.get("details", {})),
            }
        else:
            components[component] = {"status": "unknown", "age_seconds": None, "last_seen": None, "details": {}}

    latest_window = await database[Collections.METRICS_GLOBAL].find_one({}, sort=[("window_start", -1)])
    lag = None
    if latest_window:
        start = latest_window["window_start"]
        start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        lag = round((datetime.now(timezone.utc) - start).total_seconds(), 1)

    return {
        "status": (snapshot or {}).get("status", "unknown"),
        "updated_at": jsonable((snapshot or {}).get("ts")),
        "components": components,
        "pipeline_lag_seconds": lag,
        "details": jsonable((snapshot or {}).get("details", {})),
    }


@router.get("/system/health/history", summary="Health samples over time")
async def health_history(window: TimeRange = Depends(time_range), component: str = Query("platform")):
    cursor = (
        db()[Collections.SYSTEM_HEALTH]
        .find({"component": component, "ts": {"$gte": window.start, "$lte": window.end}},
              {"_id": 0, "details.components": 0})
        .sort("ts", 1)
        .limit(2000)
    )
    return {"range": window.as_dict(), "component": component, "items": docs([row async for row in cursor])}


@router.get("/system/pipeline", summary="Streaming pipeline telemetry")
async def pipeline():
    database = db()
    queries = await database[Collections.CONFIG].find_one({"key": "spark_queries"})
    generator = await database[Collections.CONFIG].find_one({"key": "generator_state"})
    backfill = await database[Collections.CONFIG].find_one({"key": "backfill"})

    since = datetime.now(timezone.utc) - timedelta(minutes=15)
    batches = [
        {
            "query": row["_id"],
            "batches": row["batches"],
            "rows": row["rows"],
            "anomalies": row["anomalies"],
            "avg_duration_ms": round(row["avg_duration"] or 0, 1),
            "max_duration_ms": round(row["max_duration"] or 0, 1),
        }
        async for row in database[Collections.PIPELINE_STATS].aggregate(
            [
                {"$match": {"ts": {"$gte": since}}},
                {
                    "$group": {
                        "_id": "$query",
                        "batches": {"$sum": 1},
                        "rows": {"$sum": "$rows"},
                        "anomalies": {"$sum": "$anomalies"},
                        "avg_duration": {"$avg": "$duration_ms"},
                        "max_duration": {"$max": "$duration_ms"},
                    }
                },
                {"$sort": {"rows": -1}},
            ]
        )
    ]

    throughput = [
        {
            "t": jsonable(row["_id"]),
            "rows": row["rows"],
            "duration_ms": round(row["duration"] or 0, 1),
        }
        async for row in database[Collections.PIPELINE_STATS].aggregate(
            [
                {"$match": {"ts": {"$gte": since}}},
                {
                    "$group": {
                        "_id": {"$dateTrunc": {"date": "$ts", "unit": "second", "binSize": 60}},
                        "rows": {"$sum": "$rows"},
                        "duration": {"$avg": "$duration_ms"},
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
    ]

    return {
        "spark_queries": jsonable((queries or {}).get("value", {})),
        "spark_updated_at": jsonable((queries or {}).get("updated_at")),
        "generator": jsonable((generator or {}).get("value", {})),
        "generator_updated_at": jsonable((generator or {}).get("updated_at")),
        "backfill": jsonable((backfill or {}).get("value")),
        "batches_15m": batches,
        "throughput": throughput,
        "config": {
            "kafka_bootstrap": settings.kafka.bootstrap_servers,
            "topics": {
                "raw": settings.kafka.topic_raw,
                "enriched": settings.kafka.topic_enriched,
                "anomalies": settings.kafka.topic_anomalies,
            },
            "window": settings.spark.window_metric,
            "watermark": settings.spark.watermark_delay,
            "trigger": settings.spark.trigger_interval,
            "shuffle_partitions": settings.spark.shuffle_partitions,
        },
    }


@router.get("/system/stats", summary="Storage footprint per collection")
async def system_stats():
    database = db()
    counts = {}
    for name in [
        Collections.RAW_LOGS, Collections.METRICS_GLOBAL, Collections.METRICS_ENDPOINT,
        Collections.METRICS_IP, Collections.METRICS_GEO, Collections.METRICS_SERVICE,
        Collections.SESSIONS, Collections.ANOMALIES, Collections.INCIDENTS, Collections.ALERTS,
    ]:
        counts[name] = await database[name].estimated_document_count()
    try:
        stats = await database.command("dbstats")
    except Exception:
        stats = {}
    return {
        "collections": counts,
        "storage_mb": round(stats.get("storageSize", 0) / 1_048_576, 2),
        "data_mb": round(stats.get("dataSize", 0) / 1_048_576, 2),
        "index_mb": round(stats.get("indexSize", 0) / 1_048_576, 2),
        "objects": stats.get("objects", 0),
    }


# ── Runtime configuration ────────────────────────────────────────────────────
@router.get("/config/thresholds", summary="Live detector thresholds")
async def get_thresholds():
    defaults = settings.detection.as_dict()
    stored = await db()[Collections.CONFIG].find_one({"key": "thresholds"})
    overrides = (stored or {}).get("value", {}) or {}
    effective = {**defaults, **{k: v for k, v in overrides.items() if k in defaults}}
    return {
        "values": effective,
        "defaults": defaults,
        "overrides": overrides,
        "bounds": {k: {"min": v[0], "max": v[1]} for k, v in THRESHOLD_BOUNDS.items()},
        "meta": THRESHOLD_META,
        "updated_at": jsonable((stored or {}).get("updated_at")),
    }


@router.put("/config/thresholds", summary="Update detector thresholds")
async def put_thresholds(payload: Dict[str, Any] = Body(...)):
    defaults = settings.detection.as_dict()
    updates: Dict[str, float] = {}
    errors: List[str] = []

    for key, value in payload.items():
        if key not in defaults:
            errors.append(f"unknown threshold '{key}'")
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            errors.append(f"'{key}' must be numeric")
            continue
        low, high = THRESHOLD_BOUNDS.get(key, (float("-inf"), float("inf")))
        if not low <= numeric <= high:
            errors.append(f"'{key}' must be between {low} and {high}")
            continue
        updates[key] = numeric

    severity_order = ["severity_low", "severity_medium", "severity_high", "severity_critical"]
    merged = {**defaults, **updates}
    values = [merged[key] for key in severity_order]
    if values != sorted(values):
        errors.append("severity thresholds must increase: low < medium < high < critical")

    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    await db()[Collections.CONFIG].update_one(
        {"key": "thresholds"},
        {"$set": {"value": updates, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"values": {**defaults, **updates}, "applied": updates, "note": "detectors reload thresholds within 30s"}


@router.delete("/config/thresholds", summary="Reset thresholds to defaults")
async def reset_thresholds():
    await db()[Collections.CONFIG].delete_one({"key": "thresholds"})
    return {"values": settings.detection.as_dict(), "reset": True}


@router.get("/config/alert-rules", summary="Alert rules")
async def list_rules():
    cursor = db()[Collections.ALERT_RULES].find({}, {"_id": 0})
    rules = [row async for row in cursor]
    if not rules:
        rules = DEFAULT_ALERT_RULES
    return {"items": docs(rules)}


@router.put("/config/alert-rules/{rule_id}", summary="Create or update an alert rule")
async def upsert_rule(rule_id: str, payload: Dict[str, Any] = Body(...)):
    allowed = {"name", "description", "enabled", "match", "channels", "cooldown_seconds"}
    update = {key: value for key, value in payload.items() if key in allowed}
    if not update:
        raise HTTPException(status_code=400, detail=f"payload must contain one of {sorted(allowed)}")
    if "cooldown_seconds" in update:
        update["cooldown_seconds"] = max(10, int(update["cooldown_seconds"]))
    update["updated_at"] = datetime.now(timezone.utc)
    await db()[Collections.ALERT_RULES].update_one(
        {"rule_id": rule_id}, {"$set": update, "$setOnInsert": {"rule_id": rule_id}}, upsert=True
    )
    return doc(await db()[Collections.ALERT_RULES].find_one({"rule_id": rule_id}, {"_id": 0}))


@router.delete("/config/alert-rules/{rule_id}", summary="Delete an alert rule")
async def delete_rule(rule_id: str):
    result = await db()[Collections.ALERT_RULES].delete_one({"rule_id": rule_id, "builtin": {"$ne": True}})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="rule not found or is built-in")
    return {"rule_id": rule_id, "deleted": True}


# ── Models and detection quality ─────────────────────────────────────────────
@router.get("/system/models", summary="Trained models and measured detection quality")
async def models():
    database = db()
    cursor = database[Collections.MODELS].find({}, {"_id": 0}).sort("trained_at", -1).limit(20)
    quality = await database[Collections.CONFIG].find_one({"key": "detection_quality"})
    last_training = await database[Collections.CONFIG].find_one({"key": "last_training"})
    baselines = await database[Collections.BASELINES].find_one({"key": "traffic_profiles"})
    return {
        "models": docs([row async for row in cursor]),
        "detection_quality": jsonable((quality or {}).get("value")),
        "last_training": jsonable((last_training or {}).get("value")),
        "traffic_profiles": jsonable((baselines or {}).get("value", [])),
    }


# ── Demo control plane ───────────────────────────────────────────────────────
@router.get("/system/scenarios", summary="Injectable incident scenarios and what is running")
async def scenarios():
    database = db()
    catalog = await database[Collections.CONFIG].find_one({"key": "scenario_catalog"})
    state = await database[Collections.CONFIG].find_one({"key": "generator_state"})
    queued = await database[Collections.CONFIG].find_one({"key": "scenario_queue"})
    return {
        "catalog": jsonable((catalog or {}).get("value", [])),
        "active": jsonable(((state or {}).get("value", {}) or {}).get("active", [])),
        "recent": jsonable(((state or {}).get("value", {}) or {}).get("recent", [])),
        "queued": jsonable((queued or {}).get("value", [])),
        "generator": jsonable(((state or {}).get("value", {}) or {}).get("stats", {})),
    }


@router.post("/system/scenarios", summary="Inject an incident into the live stream")
async def inject_scenario(payload: Dict[str, Any] = Body(...)):
    """Queue a scenario; the generator picks it up within ~2 seconds.

    This is the demo lever: it makes anomalies happen on cue while still going
    through the real Kafka → Spark → detection path.
    """
    scenario_type = payload.get("type")
    if not scenario_type:
        raise HTTPException(status_code=400, detail="'type' is required")

    database = db()
    catalog = await database[Collections.CONFIG].find_one({"key": "scenario_catalog"})
    known = {entry["type"] for entry in (catalog or {}).get("value", [])} if catalog else set()
    if known and scenario_type not in known:
        raise HTTPException(status_code=400, detail=f"unknown scenario '{scenario_type}'; known: {sorted(known)}")

    intensity = float(payload.get("intensity", 1.0))
    if not 0.1 <= intensity <= 3.0:
        raise HTTPException(status_code=400, detail="intensity must be between 0.1 and 3.0")
    duration = payload.get("duration")
    if duration is not None:
        duration = float(duration)
        if not 30 <= duration <= 1800:
            raise HTTPException(status_code=400, detail="duration must be between 30 and 1800 seconds")

    request = {
        "type": scenario_type,
        "intensity": intensity,
        "duration": duration,
        "requested_at": datetime.now(timezone.utc),
    }
    await database[Collections.CONFIG].update_one(
        {"key": "scenario_queue"},
        {"$push": {"value": request}, "$set": {"updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"queued": jsonable(request), "note": "the generator polls this queue every ~2s"}


@router.get("/system/info", summary="Static platform description")
async def info():
    return {
        "name": "LogLens",
        "version": "1.0.0",
        "environment": settings.environment,
        "pipeline": ["log sources", "kafka", "spark structured streaming", "detection", "mongodb", "dashboard"],
        "window": settings.spark.window_metric,
        "watermark": settings.spark.watermark_delay,
        "session_gap": settings.spark.session_gap,
        "retention": {
            "raw_logs_seconds": settings.mongo.raw_ttl_seconds,
            "metrics_seconds": settings.mongo.metric_ttl_seconds,
        },
    }
