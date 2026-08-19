"""Background worker: correlation, alerting, health, retention and retraining.

A small cooperative scheduler runs each job on its own interval.  Every job is
individually guarded so one failing task can never stop the others.
"""

from __future__ import annotations

import argparse
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List

from loglens_common.config import settings
from loglens_common.logging_setup import setup_logging
from loglens_common.mongo import ensure_indexes, get_db, heartbeat, set_config
from loglens_common.schemas import Collections

from .alerts import AlertEngine, ensure_default_rules
from .correlator import Correlator
from .health import collect as collect_health

log = setup_logging("worker")

_running = True


def _stop(_signum, _frame):  # pragma: no cover
    global _running
    _running = False
    log.info("worker shutting down")


@dataclass
class Job:
    name: str
    interval: float
    action: Callable[[], object]
    next_run: float = 0.0
    runs: int = 0
    failures: int = 0
    last_result: object = None

    def due(self, now: float) -> bool:
        return now >= self.next_run

    def run(self, now: float) -> None:
        started = time.time()
        try:
            self.last_result = self.action()
            self.runs += 1
        except Exception:
            self.failures += 1
            log.exception("job %s failed", self.name)
        finally:
            self.next_run = now + self.interval
            elapsed = time.time() - started
            if elapsed > self.interval:
                log.warning("job %s took %.1fs, longer than its %.0fs interval", self.name, elapsed, self.interval)


def retention(db, raw_days: float, metric_days: float) -> Dict[str, int]:
    """Belt-and-braces cleanup for collections without a TTL index."""
    now = datetime.now(timezone.utc)
    removed = {}
    removed["anomalies"] = db[Collections.ANOMALIES].delete_many(
        {"window_start": {"$lt": now - timedelta(days=metric_days)}}
    ).deleted_count
    removed["incidents"] = db[Collections.INCIDENTS].delete_many(
        {"started_at": {"$lt": now - timedelta(days=metric_days)}, "status": "resolved"}
    ).deleted_count
    removed["alerts"] = db[Collections.ALERTS].delete_many(
        {"created_at": {"$lt": now - timedelta(days=metric_days)}}
    ).deleted_count
    removed["sessions"] = db[Collections.SESSIONS].delete_many(
        {"session_start": {"$lt": now - timedelta(days=raw_days)}}
    ).deleted_count
    for collection in (
        Collections.METRICS_IP,
        Collections.METRICS_ENDPOINT,
        Collections.METRICS_GLOBAL,
        Collections.METRICS_GEO,
        Collections.METRICS_STATUS,
        Collections.METRICS_SERVICE,
    ):
        removed[collection] = db[collection].delete_many(
            {"window_start": {"$lt": now - timedelta(days=metric_days)}}
        ).deleted_count
    total = sum(removed.values())
    if total:
        log.info("retention removed %d documents %s", total, {k: v for k, v in removed.items() if v})
    return removed


def retrain() -> Dict[str, bool]:
    from mlpipeline.train import build_parser as train_parser
    from mlpipeline.train import run as train_run

    args = train_parser().parse_args([])
    return train_run(args)


def evaluate_quality(hours: float = 12.0) -> Dict[str, object]:
    from mlpipeline.evaluate import evaluate

    return evaluate(get_db(), hours, "medium", 2)


def main() -> int:
    parser = argparse.ArgumentParser(prog="loglens-worker")
    parser.add_argument("--correlation-interval", type=float, default=settings.worker.correlation_interval_seconds)
    parser.add_argument("--alert-interval", type=float, default=20.0)
    parser.add_argument("--health-interval", type=float, default=settings.worker.health_interval_seconds)
    parser.add_argument("--retention-interval", type=float, default=900.0)
    parser.add_argument("--train-interval", type=float, default=settings.ml.retrain_interval_minutes * 60)
    parser.add_argument("--evaluate-interval", type=float, default=600.0)
    parser.add_argument("--raw-retention-days", type=float, default=2.0)
    parser.add_argument("--metric-retention-days", type=float, default=14.0)
    parser.add_argument("--no-training", action="store_true")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    db = get_db()
    ensure_indexes(db)
    ensure_default_rules(db)
    set_config("worker_started_at", datetime.now(timezone.utc), db=db)

    correlator = Correlator(join_window_minutes=settings.worker.incident_join_window_minutes)
    alert_engine = AlertEngine()

    jobs: List[Job] = [
        Job("correlate", args.correlation_interval, lambda: correlator.run(db)),
        Job("alerts", args.alert_interval, lambda: alert_engine.run(db)),
        Job("health", args.health_interval, lambda: collect_health(db)),
        Job("retention", args.retention_interval, lambda: retention(db, args.raw_retention_days, args.metric_retention_days)),
    ]
    if not args.no_training:
        jobs.append(Job("train", args.train_interval, retrain, next_run=time.time() + 120))
        jobs.append(Job("evaluate", args.evaluate_interval, lambda: evaluate_quality(12.0), next_run=time.time() + 180))

    log.info("worker started with jobs: %s", ", ".join(f"{j.name}@{j.interval:.0f}s" for j in jobs))

    last_heartbeat = 0.0
    while _running:
        now = time.time()
        for job in jobs:
            if job.due(now):
                job.run(now)

        if now - last_heartbeat > 15:
            try:
                heartbeat(
                    "worker",
                    "healthy" if all(j.failures == 0 for j in jobs) else "degraded",
                    {job.name: {"runs": job.runs, "failures": job.failures} for job in jobs},
                    db=db,
                )
            except Exception:  # pragma: no cover
                pass
            last_heartbeat = now

        time.sleep(1.0)

    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
