"""Historical backfill.

Streaming windows are watermarked, so replaying week-old events through Kafka
would simply drop them.  Instead this tool simulates the past directly and
writes the *same* metric documents Spark writes, using the reference
implementation in :mod:`loglens_common.aggregation`.

Running it once gives the platform, from a cold start:
  * populated historical charts and time-range filters,
  * a seasonal baseline (so time-of-day detection works immediately),
  * a labelled training set for the IsolationForest models,
  * a believable incident history.
"""

from __future__ import annotations

import argparse
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd

from loglens_common.aggregation import all_metrics
from loglens_common.analyzers import analyze_endpoints, analyze_geo, analyze_global, analyze_ips, dedupe
from loglens_common.config import settings
from loglens_common.logging_setup import setup_logging
from loglens_common.mongo import bulk_insert, bulk_upsert, ensure_indexes, get_db, get_thresholds
from loglens_common.schemas import Collections

from .engine import TrafficEngine
from .scenarios import ScenarioScheduler

log = setup_logging("backfill")

COLLECTION_KEYS = {
    "global": (Collections.METRICS_GLOBAL, ["window_start"]),
    "endpoint": (Collections.METRICS_ENDPOINT, ["window_start", "endpoint", "method"]),
    "ip": (Collections.METRICS_IP, ["window_start", "ip"]),
    "status": (Collections.METRICS_STATUS, ["window_start", "status_class"]),
    "geo": (Collections.METRICS_GEO, ["window_start", "country"]),
    "service": (Collections.METRICS_SERVICE, ["window_start", "service"]),
    "session": (Collections.SESSIONS, ["session_id", "session_start"]),
}


def simulate(args: argparse.Namespace) -> Dict[str, int]:
    db = get_db()
    ensure_indexes(db)
    thresholds = get_thresholds(db)

    rng = random.Random(args.seed)
    scheduler = ScenarioScheduler(
        rng=rng,
        enabled=not args.no_scenarios,
        mean_gap_seconds=max(3600.0 / max(args.scenarios_per_hour, 0.01), 120.0),
        max_concurrent=2,
    )
    engine = TrafficEngine(rng=rng, base_rps=args.rps * args.scale, scheduler=scheduler)

    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(hours=args.hours)
    log.info(
        "backfilling %.1fh (%s → %s) at %.0f rps (scale %.2f)",
        args.hours, start.isoformat(), end.isoformat(), args.rps * args.scale, args.scale,
    )

    chunk = timedelta(minutes=args.chunk_minutes)
    counters = {"events": 0, "raw_written": 0, "windows": 0, "anomalies": 0}
    global_history: List[Dict[str, Any]] = []
    endpoint_history: List[Dict[str, Any]] = []
    geo_history: List[Dict[str, Any]] = []
    all_anomalies: List[Dict[str, Any]] = []
    started = time.time()

    cursor = start
    while cursor < end:
        chunk_end = min(cursor + chunk, end)
        events: List[Dict[str, Any]] = []
        ts = cursor.timestamp()
        stop = chunk_end.timestamp()
        while ts < stop:
            events.extend(engine.generate(ts, args.step))
            ts += args.step

        if not events:
            cursor = chunk_end
            continue

        frame = pd.DataFrame(events)
        metrics = all_metrics(frame, "1min", "5min")
        counters["events"] += len(events)
        counters["windows"] += len(metrics["global"])

        for family, docs in metrics.items():
            collection, keys = COLLECTION_KEYS[family]
            if docs:
                bulk_upsert(collection, docs, keys, db=db)

        # Persist a sample of raw events so the Log Explorer has history too.
        if args.raw_sample > 0:
            sample = frame.sample(frac=min(args.raw_sample, 1.0), random_state=args.seed)
            if not sample.empty:
                records = sample.to_dict("records")
                for record in records:
                    record["timestamp"] = pd.to_datetime(record["timestamp"], utc=True).to_pydatetime()
                    record["ingest_ts"] = record["timestamp"]
                counters["raw_written"] += bulk_insert(Collections.RAW_LOGS, records, db=db)

        # Run the real detectors over the freshly computed windows so the
        # incident history is produced by the same code path as live traffic.
        global_frame = pd.DataFrame(metrics["global"])
        history_frame = pd.DataFrame(global_history[-720:])
        for _, row in global_frame.iterrows():
            found = analyze_global(row, history_frame, thresholds, model=None)
            all_anomalies.extend(found)
            history_frame = pd.concat([history_frame, row.to_frame().T], ignore_index=True)
        global_history.extend(metrics["global"])

        endpoint_frame = pd.DataFrame(metrics["endpoint"])
        if not endpoint_frame.empty:
            all_anomalies.extend(
                analyze_endpoints(endpoint_frame, pd.DataFrame(endpoint_history[-6000:]), thresholds)
            )
            endpoint_history.extend(metrics["endpoint"])
            endpoint_history = endpoint_history[-12000:]

        ip_frame = pd.DataFrame(metrics["ip"])
        if not ip_frame.empty:
            all_anomalies.extend(analyze_ips(ip_frame, pd.DataFrame(), thresholds, model=None))

        geo_frame = pd.DataFrame(metrics["geo"])
        if not geo_frame.empty:
            all_anomalies.extend(analyze_geo(geo_frame, pd.DataFrame(geo_history[-2000:]), thresholds))
            geo_history.extend(metrics["geo"])

        elapsed = time.time() - started
        progress = (chunk_end - start) / (end - start)
        log.info(
            "%5.1f%% | %s → %s | %s events | %s anomalies | %.0fs elapsed",
            progress * 100, cursor.strftime("%m-%d %H:%M"), chunk_end.strftime("%H:%M"),
            f"{counters['events']:,}", f"{len(all_anomalies):,}", elapsed,
        )
        cursor = chunk_end

    unique = dedupe(all_anomalies)
    if unique:
        bulk_upsert(Collections.ANOMALIES, unique, ["anomaly_id"], db=db)
    counters["anomalies"] = len(unique)

    db[Collections.CONFIG].update_one(
        {"key": "backfill"},
        {
            "$set": {
                "value": {
                    "completed_at": datetime.now(timezone.utc),
                    "hours": args.hours,
                    "events": counters["events"],
                    "windows": counters["windows"],
                    "anomalies": counters["anomalies"],
                    "scenarios": [s.to_dict() for s in scheduler.history + scheduler.active],
                }
            }
        },
        upsert=True,
    )

    log.info(
        "backfill complete: %s events, %s metric windows, %s raw samples, %s anomalies in %.0fs",
        f"{counters['events']:,}", f"{counters['windows']:,}",
        f"{counters['raw_written']:,}", f"{counters['anomalies']:,}", time.time() - started,
    )
    return counters


def rescore(args: argparse.Namespace) -> Dict[str, int]:
    """Re-run detection over metrics already in MongoDB.

    Needed whenever the detectors or their thresholds change: the expensive part
    of a backfill is simulating and aggregating traffic, and that output is
    already stored.  Re-scoring reuses it, so tuning a threshold and seeing the
    effect on historical incidents takes seconds instead of a full re-run.

    Operationally this is also how you answer "would the new configuration have
    caught last Tuesday?".
    """
    db = get_db()
    thresholds = get_thresholds(db)
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)
    log.info("re-scoring stored metrics from %s to %s", start.isoformat(), end.isoformat())

    def load(collection: str) -> pd.DataFrame:
        docs = list(
            db[collection].find({"window_start": {"$gte": start, "$lte": end}}, {"_id": 0}).sort("window_start", 1)
        )
        if not docs:
            return pd.DataFrame()
        frame = pd.DataFrame(docs)
        frame["window_start"] = pd.to_datetime(frame["window_start"], utc=True)
        if "window_end" in frame:
            frame["window_end"] = pd.to_datetime(frame["window_end"], utc=True)
        return frame

    global_frame = load(Collections.METRICS_GLOBAL)
    endpoint_frame = load(Collections.METRICS_ENDPOINT)
    ip_frame = load(Collections.METRICS_IP)
    geo_frame = load(Collections.METRICS_GEO)
    log.info(
        "loaded %s global / %s endpoint / %s ip / %s geo windows",
        f"{len(global_frame):,}", f"{len(endpoint_frame):,}", f"{len(ip_frame):,}", f"{len(geo_frame):,}",
    )
    if global_frame.empty:
        log.warning("nothing to re-score — run the backfill first")
        return {"anomalies": 0}

    found: List[Dict[str, Any]] = []

    history = pd.DataFrame()
    for _, row in global_frame.iterrows():
        found.extend(analyze_global(row, history, thresholds, model=None))
        history = pd.concat([history, row.to_frame().T], ignore_index=True).tail(1440)

    if not endpoint_frame.empty:
        for window, group in endpoint_frame.groupby("window_start", sort=True):
            past = endpoint_frame[endpoint_frame["window_start"] < window].tail(8000)
            found.extend(analyze_endpoints(group, past, thresholds))

    if not ip_frame.empty:
        for _, group in ip_frame.groupby("window_start", sort=True):
            found.extend(analyze_ips(group, pd.DataFrame(), thresholds, model=None))

    if not geo_frame.empty:
        for window, group in geo_frame.groupby("window_start", sort=True):
            past = geo_frame[geo_frame["window_start"] < window].tail(3000)
            found.extend(analyze_geo(group, past, thresholds))

    unique = dedupe(found)
    # Replace rather than merge: a re-score is authoritative for its window, and
    # leaving detections behind that the new configuration would not produce
    # would make the evaluation report meaningless.
    removed = db[Collections.ANOMALIES].delete_many({"window_start": {"$gte": start, "$lte": end}}).deleted_count
    db[Collections.INCIDENTS].delete_many({"started_at": {"$gte": start, "$lte": end}})
    if unique:
        bulk_upsert(Collections.ANOMALIES, unique, ["anomaly_id"], db=db)

    log.info("re-scored %s windows: removed %s previous, wrote %s anomalies",
             f"{len(global_frame):,}", f"{removed:,}", f"{len(unique):,}")
    return {"anomalies": len(unique), "removed": removed}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loglens-backfill", description="Generate historical metrics and incidents.")
    parser.add_argument("--hours", type=float, default=6.0, help="how far back to simulate")
    parser.add_argument("--rps", type=float, default=settings.generator.base_rps)
    parser.add_argument("--scale", type=float, default=0.6, help="volume scale for history (keeps the run fast)")
    parser.add_argument("--seed", type=int, default=settings.generator.seed + 7)
    parser.add_argument("--step", type=float, default=1.0, help="simulation step, seconds")
    parser.add_argument("--chunk-minutes", type=float, default=20.0)
    parser.add_argument("--raw-sample", type=float, default=0.015, help="fraction of raw events to persist")
    parser.add_argument("--scenarios-per-hour", type=float, default=2.5)
    parser.add_argument("--no-scenarios", action="store_true")
    parser.add_argument(
        "--detect-only",
        action="store_true",
        help="skip simulation; re-run detection over metrics already stored (use after tuning thresholds)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.detect_only:
        rescore(args)
    else:
        simulate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
