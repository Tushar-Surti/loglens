"""System-health collection: what the platform knows about itself.

Every component writes a heartbeat; this module turns those, plus Kafka lag and
MongoDB statistics, into the composite snapshot rendered on the Health page.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from loglens_common.config import settings
from loglens_common.mongo import get_db
from loglens_common.schemas import Collections

log = logging.getLogger("loglens.worker.health")

COMPONENTS = ["generator", "spark", "worker", "api"]
STALE_AFTER_SECONDS = 90


def _component_status(db, component: str) -> Dict[str, Any]:
    doc = db[Collections.SYSTEM_HEALTH].find_one(
        {"component": component}, sort=[("ts", -1)]
    )
    if not doc:
        return {"component": component, "status": "unknown", "last_seen": None, "age_seconds": None, "details": {}}
    last_seen = doc["ts"].replace(tzinfo=timezone.utc) if doc["ts"].tzinfo is None else doc["ts"]
    age = (datetime.now(timezone.utc) - last_seen).total_seconds()
    status = doc.get("status", "healthy")
    if age > STALE_AFTER_SECONDS:
        status = "stale"
    return {
        "component": component,
        "status": status,
        "last_seen": last_seen,
        "age_seconds": round(age, 1),
        "details": doc.get("details", {}),
    }


def kafka_health() -> Dict[str, Any]:
    try:
        from loglens_common.kafka_io import topic_lag

        lag = topic_lag("loglens-spark")
        total = sum(entry["lag"] for entry in lag.values())
        return {
            "status": "healthy" if total < 50_000 else "degraded",
            "bootstrap": settings.kafka.bootstrap_servers,
            "topics": lag,
            "total_lag": total,
        }
    except Exception as exc:  # pragma: no cover - broker may be down
        return {"status": "unavailable", "error": str(exc), "bootstrap": settings.kafka.bootstrap_servers}


def mongo_health(db) -> Dict[str, Any]:
    try:
        stats = db.command("dbstats")
        collections = {}
        for name in [
            Collections.RAW_LOGS,
            Collections.METRICS_GLOBAL,
            Collections.METRICS_ENDPOINT,
            Collections.METRICS_IP,
            Collections.ANOMALIES,
            Collections.INCIDENTS,
            Collections.ALERTS,
            Collections.SESSIONS,
        ]:
            collections[name] = db[name].estimated_document_count()
        return {
            "status": "healthy",
            "database": stats.get("db"),
            "collections": collections,
            "storage_mb": round(stats.get("storageSize", 0) / 1_048_576, 2),
            "data_mb": round(stats.get("dataSize", 0) / 1_048_576, 2),
            "indexes_mb": round(stats.get("indexSize", 0) / 1_048_576, 2),
            "objects": stats.get("objects", 0),
        }
    except Exception as exc:  # pragma: no cover
        return {"status": "degraded", "error": str(exc)}


def pipeline_freshness(db) -> Dict[str, Any]:
    """How stale is the newest metric window?  The single best liveness signal."""
    latest = db[Collections.METRICS_GLOBAL].find_one({}, sort=[("window_start", -1)])
    if not latest:
        return {"status": "no_data", "lag_seconds": None}
    window_start = latest["window_start"]
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    lag = (datetime.now(timezone.utc) - window_start).total_seconds()
    status = "healthy" if lag < 180 else ("degraded" if lag < 600 else "stalled")
    return {
        "status": status,
        "latest_window": window_start,
        "lag_seconds": round(lag, 1),
        "requests_last_window": latest.get("requests", 0),
        "rps": latest.get("rps", 0),
    }


def throughput(db, minutes: int = 5) -> Dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    pipeline = [
        {"$match": {"window_start": {"$gte": since}}},
        {
            "$group": {
                "_id": None,
                "requests": {"$sum": "$requests"},
                "windows": {"$sum": 1},
                "avg_ingest_latency_ms": {"$avg": "$avg_ingest_latency_ms"},
            }
        },
    ]
    result = list(db[Collections.METRICS_GLOBAL].aggregate(pipeline))
    if not result:
        return {"events_per_second": 0.0, "windows": 0}
    row = result[0]
    return {
        "events_per_second": round(row.get("requests", 0) / (minutes * 60), 2),
        "requests": row.get("requests", 0),
        "windows": row.get("windows", 0),
        "avg_ingest_latency_ms": round(row.get("avg_ingest_latency_ms") or 0.0, 1),
    }


def collect(db=None) -> Dict[str, Any]:
    db = get_db() if db is None else db
    components = [_component_status(db, component) for component in COMPONENTS]
    kafka = kafka_health()
    mongo = mongo_health(db)
    freshness = pipeline_freshness(db)
    rates = throughput(db)

    degraded = [c["component"] for c in components if c["status"] in ("degraded", "stale", "unknown")]
    overall = "healthy"
    if freshness["status"] in ("stalled", "no_data") or kafka["status"] == "unavailable":
        overall = "critical"
    elif degraded or freshness["status"] == "degraded" or kafka["status"] == "degraded":
        overall = "degraded"

    snapshot = {
        "ts": datetime.now(timezone.utc),
        "component": "platform",
        "status": overall,
        "details": {
            "components": components,
            "kafka": kafka,
            "mongodb": mongo,
            "pipeline": freshness,
            "throughput": rates,
            "degraded_components": degraded,
        },
    }
    db[Collections.SYSTEM_HEALTH].insert_one(dict(snapshot))
    return snapshot
