"""``foreachBatch`` sinks: persistence, detection and pipeline telemetry.

Two very different write patterns live here, on purpose:

* **Raw events** are high-volume, so they are written *from the executors* with
  ``foreachPartition`` — the data never passes through the driver.
* **Aggregates** are tiny (a handful of rows per window), so they are collected
  to the driver with ``toPandas`` where the detection ensemble runs.

Detection only fires for **closed** windows (``window_end`` older than the
watermark).  Structured Streaming re-emits an open window on every trigger; a
partially-filled window would otherwise look like a traffic collapse.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from loglens_common.analyzers import (
    analyze_endpoints,
    analyze_geo,
    analyze_global,
    analyze_ips,
    analyze_sessions,
    dedupe,
)
from loglens_common.config import settings
from loglens_common.detectors import ModelBundle, gini, normalized_entropy
from loglens_common.mongo import bulk_upsert, get_db, get_thresholds
from loglens_common.schemas import Collections

from .history import MetricHistory
from .transforms import TRACKED_METHODS, TRACKED_STATUSES

log = logging.getLogger("loglens.streaming.sinks")

_DURATION_RE = re.compile(r"(\d+)\s*(second|minute|hour)s?", re.IGNORECASE)


def duration_seconds(spec: str, default: float = 120.0) -> float:
    match = _DURATION_RE.search(spec or "")
    if not match:
        return default
    amount, unit = int(match.group(1)), match.group(2).lower()
    return amount * {"second": 1, "minute": 60, "hour": 3600}[unit]


WATERMARK_SECONDS = duration_seconds(settings.spark.watermark_delay, 120.0)


# ── Executor-side raw writer ─────────────────────────────────────────────────
_executor_clients: Dict[str, Any] = {}


def _executor_collection(name: str, uri: str, database: str):
    """One Mongo client per (process, URI), reused across tasks.

    The connection string is passed in rather than read from the environment.
    Executors are launched by the Spark *worker*, so they inherit the worker
    container's environment — not the driver's. Reading ``settings.mongo.uri``
    here silently resolved to localhost inside the executor and every write
    failed. Passing it through the closure makes the task self-contained, which
    is the only thing that is reliable across deployment modes.
    """
    client = _executor_clients.get(uri)
    if client is None:
        from pymongo import MongoClient

        client = MongoClient(uri, serverSelectionTimeoutMS=8000, maxPoolSize=8, tz_aware=True)
        _executor_clients[uri] = client
    return client[database][name]


RAW_DROP_COLUMNS = {
    "kafka_ts", "kafka_partition", "kafka_offset", "event_time", "apdex_weight",
    "schema_version", "query", "protocol",
}


def make_raw_writer(uri: str, database: str, batch_size: int = 2000):
    """Build the executor-side writer, closing over the connection settings."""

    def write_raw_partition(rows: Iterable) -> None:
        from pymongo.errors import BulkWriteError

        collection = _executor_collection(Collections.RAW_LOGS, uri, database)
        buffer: List[Dict[str, Any]] = []
        failures: List[str] = []

        def flush() -> None:
            nonlocal buffer
            if not buffer:
                return
            try:
                collection.insert_many(buffer, ordered=False)
            except BulkWriteError:
                # Duplicate event_id: at-least-once redelivery, which is expected.
                pass
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
            buffer = []

        for row in rows:
            doc = row.asDict(recursive=True)
            event_time = doc.get("event_time")
            for column in RAW_DROP_COLUMNS:
                doc.pop(column, None)
            doc["timestamp"] = event_time
            doc["ingested_at"] = datetime.now(timezone.utc)
            buffer.append(doc)
            if len(buffer) >= batch_size:
                flush()
        flush()

        # Never fail silently: a write path that swallows its own errors looks
        # identical to an idle pipeline, which is how this went unnoticed.
        if failures:
            raise RuntimeError(
                f"raw log write failed for {len(failures)} batch(es); first error: {failures[0]}"
            )

    return write_raw_partition


# ── Shared sink plumbing ─────────────────────────────────────────────────────
class BaseSink:
    """Common behaviour: telemetry, threshold refresh, anomaly persistence."""

    name = "sink"

    def __init__(self, publish_anomalies: bool = True):
        self._db = None
        self._thresholds: Dict[str, float] = {}
        self._thresholds_at = 0.0
        self._producer = None
        self.publish_anomalies = publish_anomalies

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    def thresholds(self) -> Dict[str, float]:
        now = time.time()
        if now - self._thresholds_at > 30:
            self._thresholds = get_thresholds(self.db)
            self._thresholds_at = now
        return self._thresholds

    def producer(self):
        if self._producer is None and self.publish_anomalies:
            try:
                from loglens_common.kafka_io import make_producer

                self._producer = make_producer(**{"client.id": f"loglens-{self.name}"})
            except Exception as exc:  # pragma: no cover - Kafka publish is best-effort
                log.warning("anomaly producer unavailable: %s", exc)
                self.publish_anomalies = False
        return self._producer

    def closed(self, frame: pd.DataFrame, column: str = "window_end") -> pd.DataFrame:
        if frame.empty or column not in frame:
            return frame.iloc[0:0]
        cutoff = pd.Timestamp(datetime.now(timezone.utc) - timedelta(seconds=WATERMARK_SECONDS))
        stamps = pd.to_datetime(frame[column], utc=True)
        return frame[stamps <= cutoff]

    def save_anomalies(self, anomalies: List[Dict[str, Any]]) -> int:
        unique = dedupe(anomalies)
        if not unique:
            return 0
        bulk_upsert(Collections.ANOMALIES, unique, ["anomaly_id"], db=self.db)
        producer = self.producer()
        if producer is not None:
            from loglens_common.kafka_io import json_serializer

            for anomaly in unique:
                try:
                    producer.produce(
                        settings.kafka.topic_anomalies,
                        key=anomaly["entity"].encode("utf-8"),
                        value=json_serializer(anomaly),
                    )
                except Exception:  # pragma: no cover
                    break
            producer.poll(0)
        severities = {}
        for anomaly in unique:
            severities[anomaly["severity"]] = severities.get(anomaly["severity"], 0) + 1
        log.warning("%s emitted %d anomalies %s", self.name, len(unique), severities)
        return len(unique)

    def telemetry(self, batch_id: int, rows: int, anomalies: int, started: float) -> None:
        try:
            self.db[Collections.PIPELINE_STATS].insert_one(
                {
                    "ts": datetime.now(timezone.utc),
                    "query": self.name,
                    "batch_id": int(batch_id),
                    "rows": int(rows),
                    "anomalies": int(anomalies),
                    "duration_ms": round((time.time() - started) * 1000, 1),
                }
            )
        except Exception:  # pragma: no cover
            pass

    def __call__(self, batch_df, batch_id: int) -> None:
        started = time.time()
        try:
            rows, anomalies = self.process(batch_df, batch_id)
            self.telemetry(batch_id, rows, anomalies, started)
        except Exception:
            # A failing sink must not kill the whole streaming application.
            log.exception("%s failed on batch %s", self.name, batch_id)


# ── Raw sink ─────────────────────────────────────────────────────────────────
class RawLogSink(BaseSink):
    name = "raw_logs"

    def __init__(self, uri: Optional[str] = None, database: Optional[str] = None):
        super().__init__(publish_anomalies=False)
        self.uri = uri or settings.mongo.uri
        self.database = database or settings.mongo.database

    def process(self, batch_df, batch_id: int):
        batch_df.foreachPartition(make_raw_writer(self.uri, self.database))
        return -1, 0  # row count intentionally not collected (would force a job)


# ── Global metrics ───────────────────────────────────────────────────────────
def _global_doc(row: pd.Series, window_seconds: float) -> Dict[str, Any]:
    requests = int(row.get("requests", 0) or 0)
    errors_4xx = int(row.get("errors_4xx", 0) or 0)
    errors_5xx = int(row.get("errors_5xx", 0) or 0)
    bytes_sent = int(row.get("bytes_sent", 0) or 0)
    denominator = max(requests, 1)
    status_counts = {
        str(code): int(row.get(f"status_{code}", 0) or 0)
        for code in TRACKED_STATUSES
        if int(row.get(f"status_{code}", 0) or 0) > 0
    }
    method_counts = {
        method: int(row.get(f"method_{method}", 0) or 0)
        for method in TRACKED_METHODS
        if int(row.get(f"method_{method}", 0) or 0) > 0
    }
    return {
        "window_start": row["window_start"].to_pydatetime(),
        "window_end": row["window_end"].to_pydatetime(),
        "requests": requests,
        "rps": round(requests / window_seconds, 3),
        "unique_ips": int(row.get("unique_ips", 0) or 0),
        "unique_sessions": int(row.get("unique_sessions", 0) or 0),
        "unique_users": int(row.get("unique_users", 0) or 0),
        "unique_endpoints": int(row.get("unique_endpoints", 0) or 0),
        "errors_4xx": errors_4xx,
        "errors_5xx": errors_5xx,
        "error_rate": round((errors_4xx + errors_5xx) / denominator, 5),
        "server_error_rate": round(errors_5xx / denominator, 5),
        "success_rate": round(1 - (errors_4xx + errors_5xx) / denominator, 5),
        "bytes_sent": bytes_sent,
        "bytes_per_request": round(bytes_sent / denominator, 2),
        "avg_response_time": round(float(row.get("avg_response_time", 0) or 0), 2),
        "p50_response_time": round(float(row.get("p50_response_time", 0) or 0), 2),
        "p90_response_time": round(float(row.get("p90_response_time", 0) or 0), 2),
        "p95_response_time": round(float(row.get("p95_response_time", 0) or 0), 2),
        "p99_response_time": round(float(row.get("p99_response_time", 0) or 0), 2),
        "max_response_time": round(float(row.get("max_response_time", 0) or 0), 2),
        "apdex": round(float(row.get("apdex", 1) or 1), 4),
        "bot_requests": int(row.get("bot_requests", 0) or 0),
        "bot_ratio": round(int(row.get("bot_requests", 0) or 0) / denominator, 4),
        "cache_hit_rate": round(int(row.get("cache_hits", 0) or 0) / denominator, 4),
        "avg_ingest_latency_ms": round(float(row.get("avg_ingest_latency_ms", 0) or 0), 1),
        "status_counts": status_counts,
        "method_counts": method_counts,
        "dominant_attack_label": row.get("dominant_attack_label"),
        "updated_at": datetime.now(timezone.utc),
    }


class GlobalMetricSink(BaseSink):
    name = "metrics_global"

    def __init__(self, window_seconds: float = 60.0, model_path: Optional[str] = None):
        super().__init__()
        self.window_seconds = window_seconds
        self.history = MetricHistory(Collections.METRICS_GLOBAL, lookback_minutes=4320, max_rows=8000)
        self.model_path = model_path
        self._model: Optional[ModelBundle] = None
        self._model_at = 0.0
        self._detected: set = set()

    def model(self) -> Optional[ModelBundle]:
        if not self.model_path:
            return None
        if self._model is None or time.time() - self._model_at > 600:
            self._model = ModelBundle.load(self.model_path, "global")
            self._model_at = time.time()
        return self._model if self._model and self._model.ready else None

    def process(self, batch_df, batch_id: int):
        pdf = batch_df.toPandas()
        if pdf.empty:
            return 0, 0

        docs = [_global_doc(row, self.window_seconds) for _, row in pdf.iterrows()]
        bulk_upsert(Collections.METRICS_GLOBAL, docs, ["window_start"], db=self.db)
        self._write_status_docs(docs)

        closed = self.closed(pdf)
        if closed.empty:
            return len(docs), 0

        history = self.history.refresh(self.db)
        thresholds = self.thresholds()
        model = self.model()
        anomalies: List[Dict[str, Any]] = []

        for _, row in closed.iterrows():
            key = row["window_start"]
            if key in self._detected:
                continue
            # Re-read the merged document: the IP sink contributes the source
            # concentration/entropy fields that the DDoS detector needs.
            stored = self.db[Collections.METRICS_GLOBAL].find_one({"window_start": key.to_pydatetime()})
            if not stored:
                continue
            current = pd.Series(stored)
            past = history[history["window_start"] < pd.Timestamp(key)] if not history.empty else history
            anomalies.extend(analyze_global(current, past, thresholds, model))
            self._detected.add(key)

        if len(self._detected) > 5000:
            self._detected = set(sorted(self._detected)[-2000:])

        return len(docs), self.save_anomalies(anomalies)

    def _write_status_docs(self, docs: List[Dict[str, Any]]) -> None:
        """Derive the per-status-class rollup from the global breakdown."""
        status_docs: List[Dict[str, Any]] = []
        for doc in docs:
            by_class: Dict[str, Dict[str, int]] = {}
            for code, count in doc["status_counts"].items():
                by_class.setdefault(f"{code[0]}xx", {})[code] = count
            for status_class, codes in by_class.items():
                status_docs.append(
                    {
                        "window_start": doc["window_start"],
                        "window_end": doc["window_end"],
                        "status_class": status_class,
                        "requests": sum(codes.values()),
                        "codes": codes,
                        "updated_at": doc["updated_at"],
                    }
                )
        if status_docs:
            bulk_upsert(Collections.METRICS_STATUS, status_docs, ["window_start", "status_class"], db=self.db)


# ── Endpoint metrics ─────────────────────────────────────────────────────────
class EndpointMetricSink(BaseSink):
    name = "metrics_endpoint"

    def __init__(self):
        super().__init__()
        self.history = MetricHistory(Collections.METRICS_ENDPOINT, lookback_minutes=720, max_rows=40_000)

    def process(self, batch_df, batch_id: int):
        pdf = batch_df.toPandas()
        if pdf.empty:
            return 0, 0

        docs: List[Dict[str, Any]] = []
        for _, row in pdf.iterrows():
            requests = int(row.get("requests", 0) or 0)
            denominator = max(requests, 1)
            errors_4xx = int(row.get("errors_4xx", 0) or 0)
            errors_5xx = int(row.get("errors_5xx", 0) or 0)
            docs.append(
                {
                    "window_start": row["window_start"].to_pydatetime(),
                    "window_end": row["window_end"].to_pydatetime(),
                    "endpoint": row["endpoint"],
                    "method": row["method"],
                    "service": row.get("service", "unknown"),
                    "requests": requests,
                    "errors_4xx": errors_4xx,
                    "errors_5xx": errors_5xx,
                    "error_rate": round((errors_4xx + errors_5xx) / denominator, 5),
                    "server_error_rate": round(errors_5xx / denominator, 5),
                    "avg_response_time": round(float(row.get("avg_response_time", 0) or 0), 2),
                    "p50_response_time": round(float(row.get("p50_response_time", 0) or 0), 2),
                    "p90_response_time": round(float(row.get("p90_response_time", 0) or 0), 2),
                    "p95_response_time": round(float(row.get("p95_response_time", 0) or 0), 2),
                    "p99_response_time": round(float(row.get("p99_response_time", 0) or 0), 2),
                    "max_response_time": round(float(row.get("max_response_time", 0) or 0), 2),
                    "apdex": round(float(row.get("apdex", 1) or 1), 4),
                    "unique_ips": int(row.get("unique_ips", 0) or 0),
                    "unique_sessions": int(row.get("unique_sessions", 0) or 0),
                    "bytes_sent": int(row.get("bytes_sent", 0) or 0),
                    "cache_hit_rate": round(int(row.get("cache_hits", 0) or 0) / denominator, 4),
                    "dominant_attack_label": row.get("dominant_attack_label"),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        bulk_upsert(Collections.METRICS_ENDPOINT, docs, ["window_start", "endpoint", "method"], db=self.db)

        closed = self.closed(pd.DataFrame(docs))
        if closed.empty:
            return len(docs), 0

        history = self.history.refresh(self.db)
        anomalies = analyze_endpoints(closed, history, self.thresholds())
        return len(docs), self.save_anomalies(anomalies)


# ── IP metrics ───────────────────────────────────────────────────────────────
class IpMetricSink(BaseSink):
    """Rolls (window, ip, endpoint) rows up to per-IP behavioural features."""

    name = "metrics_ip"

    def __init__(self, window_seconds: float = 60.0, model_path: Optional[str] = None):
        super().__init__()
        self.window_seconds = window_seconds
        self.model_path = model_path
        self._model: Optional[ModelBundle] = None
        self._model_at = 0.0

    def model(self) -> Optional[ModelBundle]:
        if not self.model_path:
            return None
        if self._model is None or time.time() - self._model_at > 600:
            self._model = ModelBundle.load(self.model_path, "ip")
            self._model_at = time.time()
        return self._model if self._model and self._model.ready else None

    def process(self, batch_df, batch_id: int):
        pdf = batch_df.toPandas()
        if pdf.empty:
            return 0, 0

        pdf["window_start"] = pd.to_datetime(pdf["window_start"], utc=True)
        pdf["window_end"] = pd.to_datetime(pdf["window_end"], utc=True)

        docs: List[Dict[str, Any]] = []
        window_profiles: Dict[Any, Dict[str, Any]] = {}

        for (window_start, window_end, ip), group in pdf.groupby(["window_start", "window_end", "ip"], sort=False):
            requests = int(group["requests"].sum())
            bytes_sent = int(group["bytes_sent"].sum())
            errors = int(group["errors"].sum())
            span = max(
                (group["last_seen"].max() - group["first_seen"].min()).total_seconds(), 1.0
            )
            weights = group["requests"].to_numpy(dtype=float)
            avg_rt = float((group["avg_response_time"].to_numpy(dtype=float) * weights).sum() / max(weights.sum(), 1))
            entropy = normalized_entropy(weights.tolist())

            docs.append(
                {
                    "window_start": window_start.to_pydatetime(),
                    "window_end": window_end.to_pydatetime(),
                    "ip": ip,
                    "requests": requests,
                    "rps": round(requests / self.window_seconds, 3),
                    "unique_paths": int(group["endpoint"].nunique()),
                    "unique_endpoints": int(group["endpoint"].nunique()),
                    "unique_user_agents": int(group["user_agents"].max()),
                    "unique_sessions": int(group["sessions"].max()),
                    "error_ratio": round(errors / max(requests, 1), 4),
                    "not_found_ratio": round(int(group["not_found"].sum()) / max(requests, 1), 4),
                    "server_error_count": int(group["server_errors"].sum()),
                    "auth_fail_count": int(group["auth_fails"].sum()),
                    "avg_response_time": round(avg_rt, 2),
                    "max_response_time": round(float(group["max_response_time"].max()), 2),
                    "bytes_sent": bytes_sent,
                    "bytes_per_request": round(bytes_sent / max(requests, 1), 2),
                    "path_entropy": round(entropy, 4),
                    "burstiness": round(min(self.window_seconds / span, self.window_seconds), 3),
                    "bot_ratio": round(float(group["bot_ratio"].mean()), 4),
                    "country": str(group["country"].dropna().iloc[0]) if group["country"].notna().any() else "??",
                    "asn": str(group["asn"].dropna().iloc[0]) if group["asn"].notna().any() else "AS0",
                    "org": str(group["org"].dropna().iloc[0]) if group["org"].notna().any() else "unknown",
                    "sample_paths": [str(p) for p in group.nlargest(8, "requests")["endpoint"].tolist()],
                    "dominant_attack_label": next(
                        (label for label in group["attack_label"].dropna().tolist()), None
                    ),
                    "updated_at": datetime.now(timezone.utc),
                }
            )

            profile = window_profiles.setdefault(
                window_start, {"ip_counts": [], "endpoint_counts": {}, "top_ip": None, "top": 0, "window_end": window_end}
            )
            profile["ip_counts"].append(requests)
            if requests > profile["top"]:
                profile["top"], profile["top_ip"] = requests, ip
            for endpoint, count in zip(group["endpoint"], group["requests"]):
                profile["endpoint_counts"][endpoint] = profile["endpoint_counts"].get(endpoint, 0) + int(count)

        bulk_upsert(Collections.METRICS_IP, docs, ["window_start", "ip"], db=self.db)
        self._patch_global(window_profiles)

        frame = pd.DataFrame(docs)
        closed = self.closed(frame)
        if closed.empty:
            return len(docs), 0

        anomalies = analyze_ips(closed, pd.DataFrame(), self.thresholds(), self.model())
        return len(docs), self.save_anomalies(anomalies)

    def _patch_global(self, profiles: Dict[Any, Dict[str, Any]]) -> None:
        """Contribute source-concentration features to the global window doc.

        Gini over per-IP request counts and entropy over the endpoint mix are
        what let the DDoS detector distinguish "a lot of traffic" from "a lot of
        traffic from a handful of hosts hitting one route".
        """
        updates: List[Dict[str, Any]] = []
        for window_start, profile in profiles.items():
            total = sum(profile["ip_counts"]) or 1
            updates.append(
                {
                    "window_start": window_start.to_pydatetime(),
                    "ip_gini": round(gini(profile["ip_counts"]), 4),
                    "path_entropy": round(normalized_entropy(list(profile["endpoint_counts"].values())), 4),
                    "top_ip": profile["top_ip"],
                    "top_ip_share": round(profile["top"] / total, 4),
                    "distinct_source_ips": len(profile["ip_counts"]),
                }
            )
        if updates:
            bulk_upsert(Collections.METRICS_GLOBAL, updates, ["window_start"], db=self.db)


# ── Geo, service, sessions ───────────────────────────────────────────────────
class GeoMetricSink(BaseSink):
    name = "metrics_geo"

    def __init__(self):
        super().__init__()
        self.history = MetricHistory(Collections.METRICS_GEO, lookback_minutes=2880, max_rows=20_000)

    def process(self, batch_df, batch_id: int):
        pdf = batch_df.toPandas()
        if pdf.empty:
            return 0, 0
        docs = [
            {
                "window_start": row["window_start"].to_pydatetime(),
                "window_end": row["window_end"].to_pydatetime(),
                "country": row["country"] or "??",
                "country_name": row.get("country_name") or row["country"] or "Unknown",
                "requests": int(row.get("requests", 0) or 0),
                "unique_ips": int(row.get("unique_ips", 0) or 0),
                "unique_sessions": int(row.get("unique_sessions", 0) or 0),
                "error_rate": round(float(row.get("error_rate", 0) or 0), 5),
                "avg_response_time": round(float(row.get("avg_response_time", 0) or 0), 2),
                "p95_response_time": round(float(row.get("p95_response_time", 0) or 0), 2),
                "bytes_sent": int(row.get("bytes_sent", 0) or 0),
                "lat": round(float(row.get("lat", 0) or 0), 4),
                "lon": round(float(row.get("lon", 0) or 0), 4),
                "dominant_attack_label": row.get("dominant_attack_label"),
                "updated_at": datetime.now(timezone.utc),
            }
            for _, row in pdf.iterrows()
        ]
        bulk_upsert(Collections.METRICS_GEO, docs, ["window_start", "country"], db=self.db)

        closed = self.closed(pd.DataFrame(docs))
        if closed.empty:
            return len(docs), 0
        anomalies = analyze_geo(closed, self.history.refresh(self.db), self.thresholds())
        return len(docs), self.save_anomalies(anomalies)


class ServiceMetricSink(BaseSink):
    name = "metrics_service"

    def __init__(self):
        super().__init__(publish_anomalies=False)

    def process(self, batch_df, batch_id: int):
        pdf = batch_df.toPandas()
        if pdf.empty:
            return 0, 0
        docs = [
            {
                "window_start": row["window_start"].to_pydatetime(),
                "window_end": row["window_end"].to_pydatetime(),
                "service": row["service"] or "unknown",
                "requests": int(row.get("requests", 0) or 0),
                "error_rate": round(float(row.get("error_rate", 0) or 0), 5),
                "server_error_rate": round(float(row.get("server_error_rate", 0) or 0), 5),
                "avg_response_time": round(float(row.get("avg_response_time", 0) or 0), 2),
                "p95_response_time": round(float(row.get("p95_response_time", 0) or 0), 2),
                "p99_response_time": round(float(row.get("p99_response_time", 0) or 0), 2),
                "apdex": round(float(row.get("apdex", 1) or 1), 4),
                "hosts": int(row.get("hosts", 0) or 0),
                "updated_at": datetime.now(timezone.utc),
            }
            for _, row in pdf.iterrows()
        ]
        bulk_upsert(Collections.METRICS_SERVICE, docs, ["window_start", "service"], db=self.db)
        return len(docs), 0


class SessionSink(BaseSink):
    name = "sessions"

    def process(self, batch_df, batch_id: int):
        pdf = batch_df.toPandas()
        if pdf.empty:
            return 0, 0
        docs = [
            {
                "session_id": row["session_id"],
                "session_start": row["session_start"].to_pydatetime(),
                "session_end": row["session_end"].to_pydatetime(),
                "window_start": row["session_start"].to_pydatetime(),
                "window_end": row["session_end"].to_pydatetime(),
                "duration_seconds": round(float(row.get("duration_seconds", 1) or 1), 2),
                "requests": int(row.get("requests", 0) or 0),
                "requests_per_minute": round(float(row.get("requests_per_minute", 0) or 0), 2),
                "unique_endpoints": int(row.get("unique_endpoints", 0) or 0),
                "error_count": int(row.get("error_count", 0) or 0),
                "bytes_sent": int(row.get("bytes_sent", 0) or 0),
                "avg_response_time": round(float(row.get("avg_response_time", 0) or 0), 2),
                "user_id": row.get("user_id"),
                "ip": row.get("ip"),
                "country": row.get("country", "??"),
                "device": row.get("device", "desktop"),
                "browser": row.get("browser", "unknown"),
                "is_bot": bool(row.get("is_bot", False)),
                "entry_endpoint": row.get("entry_endpoint"),
                "exit_endpoint": row.get("exit_endpoint"),
                "converted": bool(row.get("converted", False)),
                "dominant_attack_label": row.get("dominant_attack_label"),
                "updated_at": datetime.now(timezone.utc),
            }
            for _, row in pdf.iterrows()
        ]
        bulk_upsert(Collections.SESSIONS, docs, ["session_id", "session_start"], db=self.db)
        anomalies = analyze_sessions(pd.DataFrame(docs), self.thresholds())
        return len(docs), self.save_anomalies(anomalies)
