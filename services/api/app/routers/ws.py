"""WebSocket endpoints: live metric ticks and a Kafka-backed log tail."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from loglens_common.config import settings
from loglens_common.schemas import Collections

from ..deps import db
from ..serialization import docs, jsonable

log = logging.getLogger("loglens.api.ws")
router = APIRouter()


class ConnectionRegistry:
    """Tracks live sockets so the health page can report fan-out."""

    def __init__(self) -> None:
        self.live: Set[WebSocket] = set()
        self.tail: Set[WebSocket] = set()

    def stats(self) -> Dict[str, int]:
        return {"live": len(self.live), "tail": len(self.tail)}


registry = ConnectionRegistry()


async def _snapshot(database, since: Optional[datetime]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    window = now - timedelta(minutes=5)

    latest = await database[Collections.METRICS_GLOBAL].find_one({}, sort=[("window_start", -1)])
    recent = (
        database[Collections.METRICS_GLOBAL]
        .find({"window_start": {"$gte": window}}, {"_id": 0})
        .sort("window_start", 1)
        .limit(10)
    )
    series = [row async for row in recent]

    anomaly_filter: Dict[str, Any] = {"detected_at": {"$gte": since or (now - timedelta(seconds=30))}}
    fresh = (
        database[Collections.ANOMALIES]
        .find(anomaly_filter, {"_id": 0, "contributing": 0, "evidence": 0})
        .sort("detected_at", -1)
        .limit(25)
    )
    anomalies = [row async for row in fresh]

    open_incidents = await database[Collections.INCIDENTS].count_documents({"status": "open"})
    health = await database[Collections.SYSTEM_HEALTH].find_one({"component": "platform"}, sort=[("ts", -1)])

    return {
        "type": "tick",
        "at": jsonable(now),
        "current": jsonable(latest) if latest else None,
        "series": docs(series),
        "anomalies": docs(anomalies),
        "open_incidents": open_incidents,
        "health": (health or {}).get("status", "unknown"),
        "connections": registry.stats(),
    }


@router.websocket("/ws/live")
async def live_feed(websocket: WebSocket, interval: float = Query(0, ge=0, le=60)):
    """Push a rolling snapshot on a fixed cadence.

    Polling MongoDB on a timer (rather than tailing a change stream) keeps this
    resilient to a standalone MongoDB and bounds the work per connection.
    """
    await websocket.accept()
    registry.live.add(websocket)
    database = db()
    tick = interval or settings.api.ws_tick_seconds
    last_seen: Optional[datetime] = None

    try:
        await websocket.send_json({"type": "hello", "interval": tick, "at": jsonable(datetime.now(timezone.utc))})
        while True:
            payload = await _snapshot(database, last_seen)
            last_seen = datetime.now(timezone.utc)
            await websocket.send_json(payload)
            await asyncio.sleep(tick)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - client vanished mid-send
        log.debug("live socket closed: %s", exc)
    finally:
        registry.live.discard(websocket)


def _matches(event: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    if filters.get("only_errors") and int(event.get("status", 0)) < 400:
        return False
    if filters.get("min_status") and int(event.get("status", 0)) < filters["min_status"]:
        return False
    if filters.get("endpoint") and filters["endpoint"] not in str(event.get("endpoint", "")):
        return False
    if filters.get("ip") and event.get("ip") != filters["ip"]:
        return False
    if filters.get("service") and event.get("service") != filters["service"]:
        return False
    if filters.get("search"):
        needle = filters["search"].lower()
        haystack = f"{event.get('path','')} {event.get('user_agent','')} {event.get('ip','')}".lower()
        if needle not in haystack:
            return False
    return True


@router.websocket("/ws/logs")
async def log_tail(
    websocket: WebSocket,
    only_errors: bool = Query(False),
    min_status: int = Query(0, ge=0, le=599),
    endpoint: Optional[str] = Query(None),
    ip: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    rate_limit: int = Query(40, ge=1, le=500, description="max events per second pushed to the client"),
):
    """Live tail straight off the enriched Kafka topic.

    Each socket gets its own consumer group so tails are independent, and the
    push rate is capped — a 5 000 rps stream would otherwise drown the browser.
    """
    await websocket.accept()
    registry.tail.add(websocket)
    filters = {
        "only_errors": only_errors,
        "min_status": min_status,
        "endpoint": endpoint,
        "ip": ip,
        "service": service,
        "search": search,
    }

    consumer = None
    try:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            settings.kafka.topic_enriched,
            bootstrap_servers=settings.kafka.bootstrap_servers,
            group_id=f"loglens-tail-{uuid.uuid4().hex[:8]}",
            auto_offset_reset="latest",
            enable_auto_commit=False,
            max_poll_records=200,
        )
        await consumer.start()
        await websocket.send_json({"type": "hello", "source": settings.kafka.topic_enriched, "filters": filters})

        budget = rate_limit
        window_started = asyncio.get_event_loop().time()
        dropped = 0

        while True:
            batches = await consumer.getmany(timeout_ms=800, max_records=200)
            now = asyncio.get_event_loop().time()
            if now - window_started >= 1.0:
                if dropped:
                    await websocket.send_json({"type": "throttled", "dropped": dropped})
                budget, window_started, dropped = rate_limit, now, 0

            for _partition, messages in batches.items():
                for message in messages:
                    try:
                        event = json.loads(message.value.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not _matches(event, filters):
                        continue
                    if budget <= 0:
                        dropped += 1
                        continue
                    budget -= 1
                    await websocket.send_json({"type": "log", "event": event})
    except WebSocketDisconnect:
        pass
    except ImportError:
        await websocket.send_json({"type": "error", "message": "aiokafka is not installed on the API service"})
    except Exception as exc:  # pragma: no cover - broker or client failure
        log.warning("log tail terminated: %s", exc)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        registry.tail.discard(websocket)
        if consumer is not None:
            try:
                await consumer.stop()
            except Exception:
                pass
