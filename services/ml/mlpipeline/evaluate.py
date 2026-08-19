"""Detection-quality evaluation against injected ground truth.

The generator stamps every synthetic incident with an ``attack_label`` that the
detectors never see.  This harness replays the stored windows and anomalies and
reports precision, recall, F1 and detection latency — per minute and per
scenario type.

Publishing these numbers on the dashboard is the honest way to present an
anomaly detector: without them, "we found 40 anomalies" means nothing.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from loglens_common.logging_setup import setup_logging
from loglens_common.mongo import get_db
from loglens_common.schemas import AnomalyType, Collections, Severity

log = setup_logging("ml.evaluate")

#: Which detector outputs count as a correct catch for each injected scenario.
SCENARIO_TO_TYPES: Dict[str, Set[str]] = {
    "traffic_spike": {AnomalyType.TRAFFIC_SPIKE, AnomalyType.MULTIVARIATE},
    "ddos_burst": {AnomalyType.DDOS_BURST, AnomalyType.SUSPICIOUS_IP, AnomalyType.TRAFFIC_SPIKE},
    "error_spike": {AnomalyType.ERROR_SPIKE},
    "latency_degradation": {AnomalyType.LATENCY_ANOMALY, AnomalyType.SLOW_ENDPOINT},
    "credential_stuffing": {AnomalyType.AUTH_ABUSE, AnomalyType.SUSPICIOUS_IP},
    "endpoint_scan": {AnomalyType.ENDPOINT_SCAN, AnomalyType.SUSPICIOUS_IP},
    "data_scraping": {AnomalyType.DATA_SCRAPING, AnomalyType.SUSPICIOUS_IP},
    "bot_surge": {AnomalyType.BOT_SURGE, AnomalyType.TRAFFIC_SPIKE, AnomalyType.SUSPICIOUS_IP},
    "service_outage": {AnomalyType.ERROR_SPIKE, AnomalyType.TRAFFIC_DROP},
    "geo_shift": {AnomalyType.GEO_ANOMALY, AnomalyType.SUSPICIOUS_IP, AnomalyType.TRAFFIC_SPIKE},
}


def _minute(value) -> datetime:
    stamp = pd.Timestamp(value)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.floor("min").to_pydatetime()


def load_ground_truth(db, since: datetime) -> Dict[datetime, str]:
    """Minute → injected scenario label, unioned across every metric family."""
    truth: Dict[datetime, str] = {}
    sources = [
        (Collections.METRICS_GLOBAL, "dominant_attack_label"),
        (Collections.METRICS_ENDPOINT, "dominant_attack_label"),
        (Collections.METRICS_IP, "dominant_attack_label"),
        (Collections.METRICS_GEO, "dominant_attack_label"),
    ]
    for collection, field in sources:
        cursor = db[collection].find(
            {"window_start": {"$gte": since}, field: {"$ne": None}},
            {"_id": 0, "window_start": 1, field: 1},
        )
        for doc in cursor:
            label = doc.get(field)
            if not label:
                continue
            truth.setdefault(_minute(doc["window_start"]), label)
    return truth


def load_detections(db, since: datetime, min_severity: str) -> Dict[datetime, List[Dict[str, Any]]]:
    allowed = {s for s, rank in Severity.ORDER.items() if rank >= Severity.ORDER[min_severity]}
    cursor = db[Collections.ANOMALIES].find(
        {"window_start": {"$gte": since}, "severity": {"$in": list(allowed)}},
        {"_id": 0, "window_start": 1, "type": 1, "severity": 1, "score": 1, "entity": 1, "detected_at": 1},
    )
    detections: Dict[datetime, List[Dict[str, Any]]] = defaultdict(list)
    for doc in cursor:
        detections[_minute(doc["window_start"])].append(doc)
    return detections


def evaluate(db, hours: float, min_severity: str, tolerance_minutes: int) -> Dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    truth = load_ground_truth(db, since)
    detections = load_detections(db, since, min_severity)

    all_minutes = sorted(
        {_minute(doc["window_start"]) for doc in db[Collections.METRICS_GLOBAL].find(
            {"window_start": {"$gte": since}}, {"_id": 0, "window_start": 1}
        )}
    )
    if not all_minutes:
        return {"error": "no metric windows in range"}

    tolerance = timedelta(minutes=tolerance_minutes)

    def detected_near(moment: datetime) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        offset = -tolerance
        while offset <= tolerance:
            found.extend(detections.get(moment + offset, []))
            offset += timedelta(minutes=1)
        return found

    tp = fp = fn = tn = 0
    per_type: Dict[str, Dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0, "detected_types": 0})
    latencies: List[float] = []
    # Detection latency is measured from the start of each *contiguous run* of
    # labelled minutes. Grouping by calendar hour (as this did originally)
    # merged separate incidents of the same type and reported the gap between
    # them as detection delay.
    incident_starts: Dict[datetime, datetime] = {}
    runs_by_label: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    open_run: Dict[str, Dict[str, Any]] = {}

    for minute in all_minutes:
        label = truth.get(minute)
        if not label:
            continue
        current_run = open_run.get(label)
        if current_run is None or (minute - current_run["minutes"][-1]) > timedelta(minutes=1):
            current_run = {"label": label, "start": minute, "minutes": []}
            runs_by_label[label].append(current_run)
            open_run[label] = current_run
        current_run["minutes"].append(minute)
        incident_starts[minute] = current_run["start"]

    for minute in all_minutes:
        label = truth.get(minute)
        hits = detections.get(minute, [])
        if label:
            relevant = SCENARIO_TO_TYPES.get(label, set())
            nearby = detected_near(minute)
            matched = [d for d in nearby if d["type"] in relevant] if relevant else nearby
            if matched:
                tp += 1
                per_type[label]["tp"] += 1
                start = incident_starts.get(minute, minute)
                first = min(_minute(d["window_start"]) for d in matched)
                latencies.append(max((first - start).total_seconds() / 60.0, 0.0))
            else:
                fn += 1
                per_type[label]["fn"] += 1
        else:
            # A detection just outside a labelled minute is not a false alarm:
            # incident effects decay across window boundaries, and the same
            # tolerance already applies when crediting a true positive. Counting
            # it as FP here would penalise the detector for the arbitrary
            # placement of a minute boundary.
            near_incident = any(
                truth.get(minute + offset)
                for offset in (
                    timedelta(minutes=m) for m in range(-tolerance_minutes, tolerance_minutes + 1)
                )
            )
            if hits and not near_incident:
                fp += 1
            elif not hits:
                tn += 1

    # ── Incident-level view ─────────────────────────────────────────────────
    # Minute-level recall punishes the ramp shoulders of a slow-onset incident:
    # a latency degradation that ramps over 25% of its duration has minutes at
    # both ends where the metric is still at baseline and there is genuinely
    # nothing to detect. The operationally meaningful question is "was each
    # incident caught, and how fast" — so report that alongside.
    incident_latencies: List[float] = []
    per_incident: Dict[str, Dict[str, int]] = defaultdict(lambda: {"caught": 0, "missed": 0})
    for label, runs in runs_by_label.items():
        relevant = SCENARIO_TO_TYPES.get(label, set())
        for run in runs:
            hits = [
                _minute(d["window_start"])
                for minute in run["minutes"]
                for d in detected_near(minute)
                if not relevant or d["type"] in relevant
            ]
            if hits:
                per_incident[label]["caught"] += 1
                incident_latencies.append(max((min(hits) - run["start"]).total_seconds() / 60.0, 0.0))
            else:
                per_incident[label]["missed"] += 1

    incidents_total = sum(v["caught"] + v["missed"] for v in per_incident.values())
    incidents_caught = sum(v["caught"] for v in per_incident.values())

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    breakdown = {
        label: {
            "true_positives": stats["tp"],
            "false_negatives": stats["fn"],
            "recall": round(stats["tp"] / max(stats["tp"] + stats["fn"], 1), 4),
        }
        for label, stats in sorted(per_type.items())
    }

    detector_counts: Dict[str, int] = defaultdict(int)
    for doc in db[Collections.ANOMALIES].find({"window_start": {"$gte": since}}, {"_id": 0, "detector": 1}):
        detector_counts[doc.get("detector", "unknown")] += 1

    report = {
        "evaluated_at": datetime.now(timezone.utc),
        "window_hours": hours,
        "min_severity": min_severity,
        "tolerance_minutes": tolerance_minutes,
        "minutes_evaluated": len(all_minutes),
        "attack_minutes": len(truth),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate": round(fp / max(fp + tn, 1), 4),
        "mean_detection_latency_minutes": round(
            sum(incident_latencies) / len(incident_latencies), 2
        ) if incident_latencies else None,
        "incidents": {
            "total": incidents_total,
            "caught": incidents_caught,
            "recall": round(incidents_caught / max(incidents_total, 1), 4),
        },
        "per_scenario": breakdown,
        "per_scenario_incidents": {
            label: {
                "caught": stats["caught"],
                "missed": stats["missed"],
                "recall": round(stats["caught"] / max(stats["caught"] + stats["missed"], 1), 4),
            }
            for label, stats in sorted(per_incident.items())
        },
        "detector_usage": dict(sorted(detector_counts.items(), key=lambda kv: kv[1], reverse=True)),
    }

    db[Collections.CONFIG].update_one(
        {"key": "detection_quality"}, {"$set": {"value": report, "updated_at": report["evaluated_at"]}}, upsert=True
    )
    return report


def _print(report: Dict[str, Any]) -> None:
    if "error" in report:
        log.error(report["error"])
        return
    confusion = report["confusion"]
    log.info("─" * 68)
    log.info("Detection quality over %.0fh (severity ≥ %s)", report["window_hours"], report["min_severity"])
    log.info("─" * 68)
    log.info("minutes evaluated : %d (%d with injected incidents)", report["minutes_evaluated"], report["attack_minutes"])
    log.info("confusion         : TP=%d FP=%d FN=%d TN=%d", confusion["tp"], confusion["fp"], confusion["fn"], confusion["tn"])
    log.info("precision         : %.3f", report["precision"])
    log.info("recall            : %.3f", report["recall"])
    log.info("F1                : %.3f", report["f1"])
    incidents = report.get("incidents") or {}
    if incidents:
        log.info(
            "incident recall   : %.3f  (%d of %d incidents caught)",
            incidents["recall"], incidents["caught"], incidents["total"],
        )
    log.info("false-positive rt : %.3f", report["false_positive_rate"])
    if report["mean_detection_latency_minutes"] is not None:
        log.info("detection latency : %.1f min", report["mean_detection_latency_minutes"])
    log.info("─" * 68)
    per_incident = report.get("per_scenario_incidents", {})
    log.info("%-22s %-14s %s", "scenario", "per-minute", "per-incident")
    for label, stats in report["per_scenario"].items():
        incident = per_incident.get(label, {})
        log.info(
            "%-22s %.2f (%d/%d)   %.2f (%d/%d)",
            label, stats["recall"], stats["true_positives"],
            stats["true_positives"] + stats["false_negatives"],
            incident.get("recall", 0.0), incident.get("caught", 0),
            incident.get("caught", 0) + incident.get("missed", 0),
        )
    log.info("─" * 68)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loglens-evaluate", description="Score detection quality against ground truth.")
    parser.add_argument("--hours", type=float, default=12.0)
    parser.add_argument("--min-severity", default=Severity.MEDIUM, choices=list(Severity.ORDER))
    parser.add_argument("--tolerance-minutes", type=int, default=2)
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = evaluate(get_db(), args.hours, args.min_severity, args.tolerance_minutes)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
