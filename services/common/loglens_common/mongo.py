"""MongoDB access layer: connection management, index bootstrap, bulk writes.

Two client flavours are exposed:
  * ``get_client()`` / ``get_db()`` — synchronous (generator, Spark, worker, CLI)
  * ``get_async_db()``             — Motor, used by the FastAPI service

Indexes are declared in one place (:data:`INDEX_SPEC`) and created idempotently
by :func:`ensure_indexes`, which is safe to call on every service start.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np

from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.errors import BulkWriteError, PyMongoError

from .config import settings
from .schemas import Collections

log = logging.getLogger("loglens.mongo")

_client: Optional[MongoClient] = None
_client_lock = threading.Lock()


def get_client(uri: Optional[str] = None) -> MongoClient:
    """Process-wide singleton client (thread-safe, fork-safe enough for Spark)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = MongoClient(
                    uri or settings.mongo.uri,
                    serverSelectionTimeoutMS=8000,
                    connectTimeoutMS=8000,
                    socketTimeoutMS=30000,
                    maxPoolSize=64,
                    retryWrites=True,
                    tz_aware=True,
                )
    return _client


def get_db(uri: Optional[str] = None):
    return get_client(uri)[settings.mongo.database]


def get_async_db(uri: Optional[str] = None):
    """Motor database handle for asyncio contexts (imported lazily)."""
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(
        uri or settings.mongo.uri,
        serverSelectionTimeoutMS=8000,
        maxPoolSize=64,
        tz_aware=True,
    )
    return client[settings.mongo.database]


# ── Index definitions ────────────────────────────────────────────────────────
# (keys, options).  ``expireAfterSeconds`` entries implement retention.
INDEX_SPEC: Dict[str, List[tuple]] = {
    Collections.RAW_LOGS: [
        ([("timestamp", DESCENDING)], {"name": "ts_desc"}),
        ([("timestamp", ASCENDING)], {"name": "ts_ttl", "expireAfterSeconds": settings.mongo.raw_ttl_seconds}),
        ([("status", ASCENDING), ("timestamp", DESCENDING)], {"name": "status_ts"}),
        ([("endpoint", ASCENDING), ("timestamp", DESCENDING)], {"name": "endpoint_ts"}),
        ([("ip", ASCENDING), ("timestamp", DESCENDING)], {"name": "ip_ts"}),
        ([("session_id", ASCENDING), ("timestamp", DESCENDING)], {"name": "session_ts"}),
        ([("service", ASCENDING), ("timestamp", DESCENDING)], {"name": "service_ts"}),
        ([("response_time_ms", DESCENDING)], {"name": "rt_desc"}),
        ([("event_id", ASCENDING)], {"name": "event_id_unique", "unique": True}),
        # Deliberately no text index. The Log Explorer searches with field
        # filters and anchored regexes, never `$text`, and a three-field text
        # index is the most expensive thing on the write path of the
        # highest-volume collection in the system.
    ],
    Collections.METRICS_GLOBAL: [
        ([("window_start", DESCENDING)], {"name": "win_desc"}),
        ([("window_start", ASCENDING)], {"name": "win_unique", "unique": True}),
    ],
    Collections.METRICS_ENDPOINT: [
        ([("window_start", DESCENDING)], {"name": "win_desc"}),
        ([("endpoint", ASCENDING), ("window_start", DESCENDING)], {"name": "endpoint_win"}),
        (
            [("endpoint", ASCENDING), ("method", ASCENDING), ("window_start", ASCENDING)],
            {"name": "endpoint_unique", "unique": True},
        ),
    ],
    Collections.METRICS_IP: [
        ([("window_start", DESCENDING)], {"name": "win_desc"}),
        ([("ip", ASCENDING), ("window_start", DESCENDING)], {"name": "ip_win"}),
        ([("ip", ASCENDING), ("window_start", ASCENDING)], {"name": "ip_unique", "unique": True}),
        ([("requests", DESCENDING)], {"name": "req_desc"}),
    ],
    Collections.METRICS_STATUS: [
        ([("window_start", DESCENDING)], {"name": "win_desc"}),
        ([("status_class", ASCENDING), ("window_start", ASCENDING)], {"name": "status_unique", "unique": True}),
    ],
    Collections.METRICS_GEO: [
        ([("window_start", DESCENDING)], {"name": "win_desc"}),
        ([("country", ASCENDING), ("window_start", ASCENDING)], {"name": "geo_unique", "unique": True}),
    ],
    Collections.METRICS_SERVICE: [
        ([("window_start", DESCENDING)], {"name": "win_desc"}),
        ([("service", ASCENDING), ("window_start", ASCENDING)], {"name": "service_unique", "unique": True}),
    ],
    Collections.SESSIONS: [
        ([("session_start", DESCENDING)], {"name": "start_desc"}),
        ([("session_id", ASCENDING), ("session_start", ASCENDING)], {"name": "session_unique", "unique": True}),
        ([("user_id", ASCENDING)], {"name": "user"}),
    ],
    Collections.ANOMALIES: [
        ([("detected_at", DESCENDING)], {"name": "detected_desc"}),
        ([("window_start", DESCENDING)], {"name": "win_desc"}),
        ([("type", ASCENDING), ("detected_at", DESCENDING)], {"name": "type_detected"}),
        ([("severity", ASCENDING), ("detected_at", DESCENDING)], {"name": "sev_detected"}),
        ([("entity_type", ASCENDING), ("entity", ASCENDING), ("detected_at", DESCENDING)], {"name": "entity_detected"}),
        ([("anomaly_id", ASCENDING)], {"name": "anomaly_unique", "unique": True}),
        ([("status", ASCENDING)], {"name": "status"}),
        ([("incident_id", ASCENDING)], {"name": "incident"}),
    ],
    Collections.INCIDENTS: [
        ([("started_at", DESCENDING)], {"name": "started_desc"}),
        ([("incident_id", ASCENDING)], {"name": "incident_unique", "unique": True}),
        ([("status", ASCENDING), ("last_seen_at", DESCENDING)], {"name": "status_last_seen"}),
        ([("entity_type", ASCENDING), ("entity", ASCENDING), ("type", ASCENDING)], {"name": "entity_type_idx"}),
    ],
    Collections.ALERTS: [
        ([("created_at", DESCENDING)], {"name": "created_desc"}),
        ([("alert_id", ASCENDING)], {"name": "alert_unique", "unique": True}),
        ([("rule_id", ASCENDING), ("created_at", DESCENDING)], {"name": "rule_created"}),
    ],
    Collections.ALERT_RULES: [([("rule_id", ASCENDING)], {"name": "rule_unique", "unique": True})],
    Collections.CONFIG: [([("key", ASCENDING)], {"name": "key_unique", "unique": True})],
    Collections.SYSTEM_HEALTH: [
        ([("ts", DESCENDING)], {"name": "ts_desc"}),
        ([("ts", ASCENDING)], {"name": "ts_ttl", "expireAfterSeconds": 259_200}),
        ([("component", ASCENDING), ("ts", DESCENDING)], {"name": "component_ts"}),
    ],
    Collections.PIPELINE_STATS: [
        ([("ts", DESCENDING)], {"name": "ts_desc"}),
        ([("ts", ASCENDING)], {"name": "ts_ttl", "expireAfterSeconds": 259_200}),
    ],
    Collections.MODELS: [([("name", ASCENDING), ("trained_at", DESCENDING)], {"name": "model_ver"})],
    Collections.BASELINES: [([("key", ASCENDING)], {"name": "baseline_unique", "unique": True})],
}


#: Indexes that existed in earlier versions and are now removed. Dropped on
#: start-up so an upgraded deployment does not keep paying for them.
RETIRED_INDEXES: Dict[str, List[str]] = {Collections.RAW_LOGS: ["log_text"]}


def ensure_indexes(db=None) -> Dict[str, List[str]]:
    """Create every declared index.  Idempotent and safe to call repeatedly."""
    db = get_db() if db is None else db
    created: Dict[str, List[str]] = {}

    for collection, names in RETIRED_INDEXES.items():
        for name in names:
            try:
                db[collection].drop_index(name)
                log.info("dropped retired index %s.%s", collection, name)
            except PyMongoError:
                pass  # already absent
    for collection, specs in INDEX_SPEC.items():
        names: List[str] = []
        for keys, options in specs:
            try:
                names.append(db[collection].create_index(keys, **options))
            except PyMongoError as exc:  # pragma: no cover - defensive
                log.warning("index %s on %s failed: %s", options.get("name"), collection, exc)
        created[collection] = names
    return created


# ── Write helpers ────────────────────────────────────────────────────────────
def bson_safe(value: Any) -> Any:
    """Convert NumPy scalars and arrays into native Python types.

    The detectors and aggregations work in NumPy/pandas, so their outputs carry
    ``np.bool_`` / ``np.float64`` / ``np.int64``.  ``np.float64`` and ``np.int64``
    happen to subclass their Python counterparts and encode fine; ``np.bool_``
    does **not**, and BSON rejects it — which fails a whole bulk write for one
    boolean buried in an evidence dictionary.

    Sanitising here, at the single point where documents enter the database,
    is the only place this can be guaranteed for every producer.
    """
    if isinstance(value, dict):
        return {key: bson_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [bson_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [bson_safe(item) for item in value.tolist()]
    if isinstance(value, float) and not np.isfinite(value):
        # NaN/±inf round-trip badly through JSON; store them as missing.
        return None
    return value


def bulk_insert(collection: str, docs: Sequence[Dict[str, Any]], db=None) -> int:
    """Unordered insert that tolerates duplicate-key races (at-least-once sinks)."""
    if not docs:
        return 0
    db = get_db() if db is None else db
    try:
        result = db[collection].insert_many([bson_safe(doc) for doc in docs], ordered=False)
        return len(result.inserted_ids)
    except BulkWriteError as exc:
        write_errors = exc.details.get("writeErrors", [])
        duplicates = [e for e in write_errors if e.get("code") == 11000]
        if len(duplicates) != len(write_errors):
            log.error("bulk insert into %s had %d non-duplicate errors", collection, len(write_errors) - len(duplicates))
        return exc.details.get("nInserted", 0)


def bulk_upsert(collection: str, docs: Iterable[Dict[str, Any]], key_fields: Sequence[str], db=None) -> int:
    """Upsert documents keyed by ``key_fields``.

    Used by every Spark aggregation sink: Structured Streaming in ``update``
    mode re-emits a window whenever late data arrives, so writes must be
    idempotent on (window_start, dimension).
    """
    db = get_db() if db is None else db
    operations: List[UpdateOne] = []
    for raw in docs:
        doc = bson_safe(raw)
        filter_ = {k: doc[k] for k in key_fields}
        payload = {k: v for k, v in doc.items() if k not in filter_}
        operations.append(UpdateOne(filter_, {"$set": payload, "$setOnInsert": filter_}, upsert=True))
    if not operations:
        return 0
    try:
        result = db[collection].bulk_write(operations, ordered=False)
        return (result.upserted_count or 0) + (result.modified_count or 0)
    except BulkWriteError as exc:
        log.warning("bulk upsert into %s partially failed: %s", collection, exc.details.get("writeErrors", [])[:2])
        return exc.details.get("nUpserted", 0) + exc.details.get("nModified", 0)


# ── Runtime configuration store ──────────────────────────────────────────────
def get_config(key: str, default: Any = None, db=None) -> Any:
    db = get_db() if db is None else db
    doc = db[Collections.CONFIG].find_one({"key": key})
    return doc.get("value", default) if doc else default


def set_config(key: str, value: Any, db=None) -> None:
    db = get_db() if db is None else db
    db[Collections.CONFIG].update_one(
        {"key": key},
        {"$set": {"value": value, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def get_thresholds(db=None) -> Dict[str, float]:
    """Live detector thresholds: env defaults overlaid with dashboard overrides."""
    base = settings.detection.as_dict()
    try:
        override = get_config("thresholds", {}, db=db) or {}
        base.update({k: v for k, v in override.items() if k in base})
    except PyMongoError as exc:  # pragma: no cover - fall back to defaults
        log.warning("threshold lookup failed, using defaults: %s", exc)
    return base


def heartbeat(component: str, status: str = "healthy", details: Optional[Dict[str, Any]] = None, db=None) -> None:
    """Record a liveness/health sample for the System Health page."""
    db = get_db() if db is None else db
    db[Collections.SYSTEM_HEALTH].insert_one(
        {
            "component": component,
            "status": status,
            "ts": datetime.now(timezone.utc),
            "details": details or {},
        }
    )


def since(minutes: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)
