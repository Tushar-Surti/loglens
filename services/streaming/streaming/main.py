"""LogLens streaming application.

Topology
--------
::

    Kafka(weblogs.raw)
        └─ parse + enrich + watermark
             ├─ Q1 raw_logs        → MongoDB (executor-side bulk writes)
             ├─ Q2 enriched        → Kafka(weblogs.enriched)   [live tail]
             ├─ Q3 window 1m       → metrics_global   + global detectors
             ├─ Q4 window 1m ×ep   → metrics_endpoint + latency/error detectors
             ├─ Q5 window 1m ×ip×ep→ metrics_ip       + behavioural detectors
             ├─ Q6 window 5m ×geo  → metrics_geo      + origin detectors
             ├─ Q7 window 1m ×svc  → metrics_service
             └─ Q8 session window  → sessions         + session detectors

Eight independent stateful queries share one SparkSession and one Kafka source.
Each query owns its checkpoint directory, so any single query can be restarted
without replaying the others.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from loglens_common.config import settings
from loglens_common.logging_setup import setup_logging
from loglens_common.mongo import ensure_indexes, get_db, heartbeat
from loglens_common.schemas import Collections

from .sinks import (
    EndpointMetricSink,
    GeoMetricSink,
    GlobalMetricSink,
    IpMetricSink,
    RawLogSink,
    ServiceMetricSink,
    SessionSink,
    duration_seconds,
)
from .spark_session import build_spark
from .transforms import (
    endpoint_metrics,
    enrich,
    enriched_for_kafka,
    geo_metrics,
    global_metrics,
    ip_endpoint_metrics,
    parse_events,
    read_kafka,
    service_metrics,
    session_metrics,
    with_watermark,
)

log = setup_logging("streaming")

_running = True


def _stop(_signum, _frame):  # pragma: no cover
    global _running
    _running = False
    log.info("shutdown requested — stopping queries gracefully")


def _checkpoint(name: str) -> str:
    return os.path.join(settings.spark.checkpoint_dir, name)


def start_queries(spark, args) -> List:
    raw = read_kafka(spark, settings.kafka.topic_raw, args.starting_offsets)
    events = enrich(parse_events(raw))
    watermarked = with_watermark(events)

    window_seconds = duration_seconds(settings.spark.window_metric, 60.0)
    trigger = {"processingTime": settings.spark.trigger_interval}
    fast_trigger = {"processingTime": args.raw_trigger}

    queries = []

    # Q1 — raw persistence (executor-side writes, never through the driver)
    queries.append(
        events.writeStream.queryName("raw_logs")
        .outputMode("append")
        .option("checkpointLocation", _checkpoint("raw_logs"))
        .foreachBatch(RawLogSink())
        .trigger(**fast_trigger)
        .start()
    )

    # Q2 — enriched stream republished for the dashboard live tail
    queries.append(
        enriched_for_kafka(events).writeStream.queryName("enriched")
        .outputMode("append")
        .format("kafka")
        .option("kafka.bootstrap.servers", settings.kafka.bootstrap_servers)
        .option("topic", settings.kafka.topic_enriched)
        .option("checkpointLocation", _checkpoint("enriched"))
        .trigger(**fast_trigger)
        .start()
    )

    # Q3 — platform-wide 1-minute metrics + global detection
    queries.append(
        global_metrics(watermarked).writeStream.queryName("metrics_global")
        .outputMode("update")
        .option("checkpointLocation", _checkpoint("metrics_global"))
        .foreachBatch(GlobalMetricSink(window_seconds, args.global_model))
        .trigger(**trigger)
        .start()
    )

    # Q4 — per-endpoint metrics + latency/error detection
    queries.append(
        endpoint_metrics(watermarked).writeStream.queryName("metrics_endpoint")
        .outputMode("update")
        .option("checkpointLocation", _checkpoint("metrics_endpoint"))
        .foreachBatch(EndpointMetricSink())
        .trigger(**trigger)
        .start()
    )

    # Q5 — per-IP behavioural profile + security detection
    queries.append(
        ip_endpoint_metrics(watermarked).writeStream.queryName("metrics_ip")
        .outputMode("update")
        .option("checkpointLocation", _checkpoint("metrics_ip"))
        .foreachBatch(IpMetricSink(window_seconds, args.ip_model))
        .trigger(**trigger)
        .start()
    )

    # Q6 — geography (coarser window: country traffic is lower-rate)
    queries.append(
        geo_metrics(watermarked).writeStream.queryName("metrics_geo")
        .outputMode("update")
        .option("checkpointLocation", _checkpoint("metrics_geo"))
        .foreachBatch(GeoMetricSink())
        .trigger(processingTime=args.geo_trigger)
        .start()
    )

    # Q7 — per-service health
    queries.append(
        service_metrics(watermarked).writeStream.queryName("metrics_service")
        .outputMode("update")
        .option("checkpointLocation", _checkpoint("metrics_service"))
        .foreachBatch(ServiceMetricSink())
        .trigger(**trigger)
        .start()
    )

    # Q8 — gap-based sessionisation (append: emitted when the session closes)
    queries.append(
        session_metrics(watermarked).writeStream.queryName("sessions")
        .outputMode("append")
        .option("checkpointLocation", _checkpoint("sessions"))
        .foreachBatch(SessionSink())
        .trigger(processingTime=args.session_trigger)
        .start()
    )

    return queries


def source_lag(progress: Dict[str, Any]) -> Dict[str, Any]:
    """Real Kafka lag, taken from Spark's own view of the source.

    Structured Streaming tracks offsets in its checkpoint and never commits to a
    consumer group, so asking the broker for "consumer group lag" returns the
    whole retained log and grows forever. The authoritative numbers are in the
    progress report: ``endOffset`` is what this query has processed and
    ``latestOffset`` is what the topic holds.
    """
    total_lag = 0
    per_topic: Dict[str, int] = {}

    for source in progress.get("sources") or []:
        try:
            processed = json.loads(source.get("endOffset") or "{}")
            available = json.loads(source.get("latestOffset") or "{}")
        except (TypeError, ValueError):
            continue
        for topic, partitions in (available or {}).items():
            done = (processed or {}).get(topic, {})
            for partition, latest in (partitions or {}).items():
                behind = max(int(latest) - int(done.get(partition, 0) or 0), 0)
                per_topic[topic] = per_topic.get(topic, 0) + behind
                total_lag += behind

    return {"total": total_lag, "topics": per_topic}


def monitor(queries: List, interval: float = 15.0) -> None:
    db = get_db()
    while _running:
        time.sleep(interval)
        snapshot: Dict[str, Any] = {}
        unhealthy = []
        for query in queries:
            progress = query.lastProgress
            if not progress:
                snapshot[query.name] = {"status": "starting", "active": query.isActive}
                continue
            snapshot[query.name] = {
                "active": query.isActive,
                "batch_id": progress.get("batchId"),
                "input_rows_per_second": round(progress.get("inputRowsPerSecond") or 0.0, 2),
                "processed_rows_per_second": round(progress.get("processedRowsPerSecond") or 0.0, 2),
                "num_input_rows": progress.get("numInputRows", 0),
                # Spark reports timings in a nested `durationMs` map; there is no
                # top-level `batchDuration`, so reading one reported 0 ms for
                # every query no matter how long the batch actually took.
                # `triggerExecution` is the wall time of the whole micro-batch.
                "batch_duration_ms": (progress.get("durationMs") or {}).get("triggerExecution", 0),
                "duration_breakdown": progress.get("durationMs") or {},
                "state_rows": sum(op.get("numRowsTotal", 0) for op in progress.get("stateOperators", [])),
                "source_lag": source_lag(progress),
                "timestamp": progress.get("timestamp"),
            }
            if not query.isActive:
                unhealthy.append(query.name)

        total_in = sum(v.get("input_rows_per_second", 0) or 0 for v in snapshot.values())
        worst_lag = max(
            ((v.get("source_lag") or {}).get("total", 0) for v in snapshot.values()), default=0
        )
        log.info(
            "pipeline ingest %.0f rows/s | %s",
            total_in,
            " ".join(
                f"{name}:{value.get('num_input_rows', 0)}" for name, value in snapshot.items()
            ),
        )

        try:
            db[Collections.CONFIG].update_one(
                {"key": "spark_queries"},
                {
                    "$set": {
                        "value": snapshot,
                        "kafka_lag": worst_lag,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
            heartbeat(
                "spark",
                "degraded" if unhealthy else "healthy",
                {
                    "queries": len(queries),
                    "inactive": unhealthy,
                    "input_rows_per_second": round(total_in, 2),
                    "kafka_lag": worst_lag,
                },
                db=db,
            )
        except Exception as exc:  # pragma: no cover
            log.warning("could not publish pipeline telemetry: %s", exc)

        if unhealthy:
            log.error("queries stopped unexpectedly: %s", unhealthy)
            for query in queries:
                if not query.isActive and query.exception():
                    log.error("%s: %s", query.name, query.exception())
            break


def main() -> int:
    parser = argparse.ArgumentParser(prog="loglens-streaming")
    parser.add_argument("--starting-offsets", default=os.getenv("SPARK_STARTING_OFFSETS", "latest"))
    parser.add_argument("--raw-trigger", default=os.getenv("SPARK_RAW_TRIGGER", "5 seconds"))
    parser.add_argument("--geo-trigger", default=os.getenv("SPARK_GEO_TRIGGER", "30 seconds"))
    parser.add_argument("--session-trigger", default=os.getenv("SPARK_SESSION_TRIGGER", "30 seconds"))
    parser.add_argument("--monitor-interval", type=float, default=15.0)
    parser.add_argument("--global-model", default=os.path.join(settings.ml.model_dir, "global_isoforest.joblib"))
    parser.add_argument("--ip-model", default=os.path.join(settings.ml.model_dir, "ip_isoforest.joblib"))
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    log.info("bootstrapping MongoDB indexes")
    ensure_indexes()

    try:
        from loglens_common.kafka_io import ensure_topics

        ensure_topics()
    except Exception as exc:  # pragma: no cover - Spark can still read existing topics
        log.warning("topic bootstrap skipped: %s", exc)

    spark = build_spark()
    log.info("watermark=%s window=%s trigger=%s",
             settings.spark.watermark_delay, settings.spark.window_metric, settings.spark.trigger_interval)

    queries = start_queries(spark, args)
    log.info("started %d streaming queries: %s", len(queries), [q.name for q in queries])
    heartbeat("spark", "healthy", {"queries": [q.name for q in queries]})

    try:
        monitor(queries, args.monitor_interval)
    finally:
        for query in queries:
            try:
                query.stop()
            except Exception:  # pragma: no cover
                pass
        spark.stop()
        log.info("streaming application stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
