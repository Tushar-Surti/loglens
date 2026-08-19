"""Kafka helpers: topic bootstrap, tuned producer, consumer factory.

``confluent-kafka`` is imported lazily so that services which never talk to
Kafka directly (Spark reads it through the JVM connector) do not need the
dependency installed.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from .config import settings

log = logging.getLogger("loglens.kafka")

TOPIC_CONFIG: Dict[str, Dict[str, Any]] = {
    # retention.ms 6h keeps the local demo disk-friendly while still allowing
    # Spark to replay from earliest after a restart.
    settings.kafka.topic_raw: {"retention.ms": "21600000", "cleanup.policy": "delete", "compression.type": "producer"},
    settings.kafka.topic_enriched: {"retention.ms": "10800000", "cleanup.policy": "delete"},
    settings.kafka.topic_anomalies: {"retention.ms": "86400000", "cleanup.policy": "delete"},
    settings.kafka.topic_alerts: {"retention.ms": "86400000", "cleanup.policy": "delete"},
}


def producer_config(**overrides: Any) -> Dict[str, Any]:
    config: Dict[str, Any] = {
        "bootstrap.servers": settings.kafka.bootstrap_servers,
        "linger.ms": settings.kafka.linger_ms,
        "batch.size": settings.kafka.batch_size,
        "compression.type": settings.kafka.compression,
        "acks": "1",
        "enable.idempotence": False,
        "queue.buffering.max.messages": 1_000_000,
        "queue.buffering.max.kbytes": 512_000,
        "message.timeout.ms": 30_000,
        "socket.keepalive.enable": True,
        "client.id": "loglens-producer",
    }
    config.update(overrides)
    return config


def make_producer(**overrides: Any):
    from confluent_kafka import Producer

    return Producer(producer_config(**overrides))


def make_consumer(group_id: str, topics: Iterable[str], auto_offset_reset: str = "latest", **overrides: Any):
    from confluent_kafka import Consumer

    config: Dict[str, Any] = {
        "bootstrap.servers": settings.kafka.bootstrap_servers,
        "group.id": group_id,
        "auto.offset.reset": auto_offset_reset,
        "enable.auto.commit": True,
        "session.timeout.ms": 30_000,
        "max.poll.interval.ms": 300_000,
        "fetch.min.bytes": 1,
    }
    config.update(overrides)
    consumer = Consumer(config)
    consumer.subscribe(list(topics))
    return consumer


def ensure_topics(timeout: float = 60.0) -> Dict[str, str]:
    """Create every platform topic if missing.  Waits for the broker to be up."""
    from confluent_kafka.admin import AdminClient, NewTopic

    admin = AdminClient({"bootstrap.servers": settings.kafka.bootstrap_servers})

    deadline = time.time() + timeout
    metadata = None
    while time.time() < deadline:
        try:
            metadata = admin.list_topics(timeout=5)
            break
        except Exception as exc:  # pragma: no cover - broker still starting
            log.info("waiting for Kafka at %s (%s)", settings.kafka.bootstrap_servers, exc)
            time.sleep(2)
    if metadata is None:
        raise RuntimeError(f"Kafka unreachable at {settings.kafka.bootstrap_servers}")

    existing = set(metadata.topics.keys())
    wanted = [
        NewTopic(
            topic=name,
            num_partitions=settings.kafka.partitions,
            replication_factor=settings.kafka.replication_factor,
            config=config,
        )
        for name, config in TOPIC_CONFIG.items()
        if name not in existing
    ]

    results: Dict[str, str] = {name: "exists" for name in TOPIC_CONFIG if name in existing}
    if wanted:
        for name, future in admin.create_topics(wanted).items():
            try:
                future.result()
                results[name] = "created"
            except Exception as exc:  # pragma: no cover - race with another service
                results[name] = f"skipped ({exc})"
    log.info("kafka topics: %s", results)
    return results


def topic_lag(group_id: str, topics: Optional[List[str]] = None) -> Dict[str, Any]:
    """Consumer-group lag per topic — surfaced on the System Health page."""
    from confluent_kafka import Consumer, TopicPartition

    consumer = Consumer(
        {
            "bootstrap.servers": settings.kafka.bootstrap_servers,
            "group.id": group_id,
            "enable.auto.commit": False,
        }
    )
    try:
        metadata = consumer.list_topics(timeout=8)
        report: Dict[str, Any] = {}
        for topic in topics or list(TOPIC_CONFIG):
            if topic not in metadata.topics:
                continue
            partitions = [TopicPartition(topic, p) for p in metadata.topics[topic].partitions]
            committed = consumer.committed(partitions, timeout=8)
            total_lag = 0
            end_total = 0
            for tp in committed:
                low, high = consumer.get_watermark_offsets(tp, timeout=8, cached=False)
                end_total += high
                offset = tp.offset if tp.offset and tp.offset >= 0 else low
                total_lag += max(high - offset, 0)
            report[topic] = {
                "partitions": len(partitions),
                "log_end_offset": end_total,
                "lag": total_lag,
            }
        return report
    finally:
        consumer.close()


def json_serializer(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")


def delivery_logger(counter: Optional[Dict[str, int]] = None) -> Callable:
    """Delivery callback that tallies failures without spamming the log."""

    def _callback(err, _msg):
        if err is not None:
            if counter is not None:
                counter["failed"] = counter.get("failed", 0) + 1
            log.warning("kafka delivery failed: %s", err)
        elif counter is not None:
            counter["delivered"] = counter.get("delivered", 0) + 1

    return _callback
