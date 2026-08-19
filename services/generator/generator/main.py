"""Log generator service: streams synthetic access logs into Kafka.

Runs a fixed-step simulation loop (default 200 ms) so throughput stays smooth
and back-pressure is visible rather than hidden behind a burst-and-sleep loop.

It also exposes a small control plane: the dashboard writes a scenario request
into Mongo (``config.scenario_queue``) and this process picks it up within a
couple of seconds, which is what powers the "inject incident" demo button.
"""

from __future__ import annotations

import argparse
import random
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loglens_common.config import settings
from loglens_common.kafka_io import delivery_logger, ensure_topics, json_serializer, make_producer
from loglens_common.logging_setup import setup_logging
from loglens_common.mongo import get_db, heartbeat
from loglens_common.schemas import Collections

from .engine import TrafficEngine
from .scenarios import CATALOG_INFO, ScenarioScheduler

log = setup_logging("generator")

_running = True


def _stop(_signum, _frame):  # pragma: no cover - signal path
    global _running
    _running = False
    log.info("shutdown signal received, draining producer…")


class ControlPlane:
    """Bridges dashboard actions to the running simulation via MongoDB."""

    def __init__(self, scheduler: ScenarioScheduler, poll_seconds: float = 2.0):
        self.scheduler = scheduler
        self.poll_seconds = poll_seconds
        self._last_poll = 0.0
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    def bootstrap(self) -> None:
        """Publish the scenario catalog so the UI can render injection options."""
        try:
            self.db[Collections.CONFIG].update_one(
                {"key": "scenario_catalog"},
                {"$set": {"value": CATALOG_INFO, "updated_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
        except Exception as exc:  # pragma: no cover - Mongo optional at boot
            log.warning("could not publish scenario catalog: %s", exc)

    def poll(self, now: float, wall: float) -> None:
        if wall - self._last_poll < self.poll_seconds:
            return
        self._last_poll = wall
        try:
            doc = self.db[Collections.CONFIG].find_one_and_update(
                {"key": "scenario_queue", "value.0": {"$exists": True}},
                {"$set": {"value": []}},
            )
        except Exception as exc:  # pragma: no cover
            log.debug("control-plane poll failed: %s", exc)
            return
        for request in (doc or {}).get("value", []) or []:
            scenario = self.scheduler.spawn(
                now,
                type_=request.get("type"),
                intensity=float(request.get("intensity", 1.0)),
                duration=float(request["duration"]) if request.get("duration") else None,
                source="dashboard",
            )
            if scenario:
                log.warning(
                    "injected scenario %s (%s) for %.0fs at intensity %.2f",
                    scenario.type, scenario.scenario_id, scenario.duration, scenario.intensity,
                )

    def publish_state(self, now: float, stats: Dict[str, Any]) -> None:
        try:
            self.db[Collections.CONFIG].update_one(
                {"key": "generator_state"},
                {
                    "$set": {
                        "value": {**self.scheduler.snapshot(now), "stats": stats},
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
        except Exception:  # pragma: no cover
            pass


def run(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    scheduler = ScenarioScheduler(
        rng=rng,
        enabled=args.scenarios,
        mean_gap_seconds=args.scenario_gap,
        max_concurrent=args.max_scenarios,
    )
    engine = TrafficEngine(rng=rng, base_rps=args.rps, scheduler=scheduler)

    producer = None
    control: Optional[ControlPlane] = None
    out_file = None

    if args.sink == "kafka":
        ensure_topics()
        producer = make_producer()
        log.info("producing to %s on %s", args.topic, settings.kafka.bootstrap_servers)
    elif args.sink == "file":
        out_file = open(args.out, "a", encoding="utf-8")
        log.info("writing NDJSON to %s", args.out)
    else:
        log.info("writing NDJSON to stdout")

    if args.control_plane:
        control = ControlPlane(scheduler)
        control.bootstrap()

    counters: Dict[str, int] = {"delivered": 0, "failed": 0}
    on_delivery = delivery_logger(counters)

    step = args.step
    sim_time = time.time()
    wall_start = time.time()
    produced = 0
    last_report = wall_start
    next_wall = wall_start

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    deadline = wall_start + args.duration if args.duration else None

    while _running:
        if deadline and time.time() >= deadline:
            log.info("duration reached")
            break

        events = engine.generate(sim_time, step)

        for event in events:
            payload = json_serializer(event)
            if producer is not None:
                while True:
                    try:
                        producer.produce(
                            topic=args.topic,
                            key=event["ip"].encode("utf-8"),
                            value=payload,
                            callback=on_delivery,
                        )
                        break
                    except BufferError:
                        # Local queue full: let librdkafka drain. Visible back-pressure
                        # beats silently dropping events.
                        producer.poll(0.2)
            elif out_file is not None:
                out_file.write(payload.decode("utf-8") + "\n")
            else:
                sys.stdout.write(payload.decode("utf-8") + "\n")
        produced += len(events)

        if producer is not None:
            producer.poll(0)

        if control is not None:
            control.poll(sim_time, time.time())

        now_wall = time.time()
        if now_wall - last_report >= args.report_every:
            elapsed = now_wall - wall_start
            rate = produced / max(elapsed, 1e-6)
            active = [s.type for s in scheduler.active]
            log.info(
                "%s events | %.0f eps | target %.0f rps | organic %s / attack %s | delivered %s failed %s | active: %s",
                f"{produced:,}", rate, engine.target_rps(sim_time),
                f"{engine.stats['organic']:,}", f"{engine.stats['attack']:,}",
                f"{counters['delivered']:,}", counters["failed"],
                ", ".join(active) or "none",
            )
            if control is not None:
                control.publish_state(
                    sim_time,
                    {
                        "produced": produced,
                        "eps": round(rate, 1),
                        "target_rps": round(engine.target_rps(sim_time), 1),
                        "organic": engine.stats["organic"],
                        "attack": engine.stats["attack"],
                        "mutated": engine.stats["mutated"],
                        "delivered": counters["delivered"],
                        "failed": counters["failed"],
                        "sessions": len(engine.sessions),
                    },
                )
                try:
                    heartbeat(
                        "generator",
                        "healthy" if counters["failed"] == 0 else "degraded",
                        {"eps": round(rate, 1), "produced": produced, "failed": counters["failed"]},
                    )
                except Exception:  # pragma: no cover
                    pass
            last_report = now_wall

        # Pace the loop against wall clock (``speed`` compresses simulated time).
        sim_time += step
        next_wall += step / max(args.speed, 1e-6)
        sleep_for = next_wall - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)
        elif sleep_for < -2.0:
            # We are badly behind: resync instead of spiralling.
            log.warning("generator behind schedule by %.1fs, resyncing", -sleep_for)
            next_wall = time.time()

    if producer is not None:
        producer.flush(15)
    if out_file is not None:
        out_file.close()

    log.info("produced %s events in %.1fs", f"{produced:,}", time.time() - wall_start)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loglens-generator",
        description="Stream realistic synthetic web access logs into Kafka.",
    )
    parser.add_argument("--rps", type=float, default=settings.generator.base_rps, help="baseline requests per second at peak-normalised load")
    parser.add_argument("--seed", type=int, default=settings.generator.seed)
    parser.add_argument("--sink", choices=["kafka", "stdout", "file"], default="kafka")
    parser.add_argument("--topic", default=settings.kafka.topic_raw)
    parser.add_argument("--out", default="./data/logs/generated.ndjson")
    parser.add_argument("--duration", type=float, default=0.0, help="seconds to run (0 = forever)")
    parser.add_argument("--step", type=float, default=0.2, help="simulation step in seconds")
    parser.add_argument("--speed", type=float, default=settings.generator.speed, help="wall-clock compression factor")
    parser.add_argument("--scenarios", dest="scenarios", action="store_true", default=settings.generator.scenarios_enabled)
    parser.add_argument("--no-scenarios", dest="scenarios", action="store_false")
    parser.add_argument("--scenario-gap", type=float, default=210.0, help="mean seconds between automatic scenarios")
    parser.add_argument("--max-scenarios", type=int, default=3)
    parser.add_argument("--control-plane", dest="control_plane", action="store_true", default=True)
    parser.add_argument("--no-control-plane", dest="control_plane", action="store_false")
    parser.add_argument("--report-every", type=float, default=10.0)
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
