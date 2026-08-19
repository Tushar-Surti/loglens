"""Anomaly → incident correlation.

Raw detections are noisy by nature: a single DDoS produces a global burst
anomaly, a dozen per-IP anomalies and an error-rate anomaly, once per minute,
for as long as it lasts.  Paging on each of those is how alert fatigue starts.

The correlator groups detections that plausibly describe *one* real event —
same failure family, same or related entity, overlapping in time — into an
incident with a timeline, an aggregate severity and an impact estimate.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from loglens_common.mongo import bulk_upsert, get_db
from loglens_common.schemas import AnomalyType, Collections, EntityType, Severity

log = logging.getLogger("loglens.worker.correlator")

#: Detections in the same family describe the same class of failure.
FAMILIES: Dict[str, str] = {
    AnomalyType.DDOS_BURST: "security",
    AnomalyType.SUSPICIOUS_IP: "security",
    AnomalyType.ENDPOINT_SCAN: "security",
    AnomalyType.AUTH_ABUSE: "security",
    AnomalyType.DATA_SCRAPING: "security",
    AnomalyType.BOT_SURGE: "security",
    AnomalyType.GEO_ANOMALY: "security",
    AnomalyType.ERROR_SPIKE: "availability",
    AnomalyType.TRAFFIC_DROP: "availability",
    AnomalyType.LATENCY_ANOMALY: "performance",
    AnomalyType.SLOW_ENDPOINT: "performance",
    AnomalyType.TRAFFIC_SPIKE: "traffic",
    AnomalyType.MULTIVARIATE: "traffic",
    AnomalyType.SESSION_ANOMALY: "behaviour",
}

FAMILY_LABEL = {
    "security": "Security",
    "availability": "Availability",
    "performance": "Performance",
    "traffic": "Traffic",
    "behaviour": "User behaviour",
}


def family_of(anomaly_type: str) -> str:
    return FAMILIES.get(anomaly_type, "other")


def _correlation_key(anomaly: Dict[str, Any]) -> Tuple[str, str, str]:
    """Entity-level grouping key.

    Per-IP security detections collapse onto one campaign-level incident
    (``ip:*``) rather than one incident per attacker address — an eight-host
    botnet is one incident, not eight.
    """
    family = family_of(anomaly["type"])
    entity_type = anomaly.get("entity_type", EntityType.GLOBAL)
    entity = anomaly.get("entity", "all-traffic")
    if family == "security" and entity_type == EntityType.IP:
        return family, entity_type, "*"
    if entity_type == EntityType.SESSION:
        return family, entity_type, "*"
    return family, entity_type, entity


def _incident_id(key: Tuple[str, str, str], started_at: datetime) -> str:
    digest = hashlib.sha1(f"{key}|{started_at.isoformat()}".encode("utf-8")).hexdigest()
    return f"inc_{digest[:18]}"


def _title(anomaly: Dict[str, Any], key: Tuple[str, str, str]) -> str:
    family, entity_type, entity = key
    label = AnomalyType.LABELS.get(anomaly["type"], anomaly["type"])
    if entity_type == EntityType.GLOBAL:
        return f"{label} across the platform"
    if entity == "*":
        return f"{label} from multiple sources"
    return f"{label} on {entity}"


class Correlator:
    def __init__(self, join_window_minutes: int = 10, resolve_after_minutes: int = 15, lookback_minutes: int = 180):
        self.join_window = timedelta(minutes=join_window_minutes)
        self.resolve_after = timedelta(minutes=resolve_after_minutes)
        self.lookback = timedelta(minutes=lookback_minutes)

    # ── main entry point ─────────────────────────────────────────────────────
    def run(self, db=None) -> Dict[str, int]:
        db = get_db() if db is None else db
        pending = list(
            db[Collections.ANOMALIES]
            .find({"incident_id": None, "window_start": {"$gte": datetime.now(timezone.utc) - self.lookback}})
            .sort("window_start", 1)
            .limit(3000)
        )
        stats = {"anomalies": len(pending), "incidents_created": 0, "incidents_updated": 0, "resolved": 0}
        if not pending:
            stats["resolved"] = self.auto_resolve(db)
            return stats

        open_incidents = {
            (i["family"], i["entity_type"], i["entity"]): i
            for i in db[Collections.INCIDENTS].find({"status": {"$in": ["open", "acknowledged"]}})
        }

        touched: Dict[str, Dict[str, Any]] = {}
        assignments: List[Tuple[str, str]] = []

        for anomaly in pending:
            key = _correlation_key(anomaly)
            window_start = anomaly["window_start"]
            if window_start.tzinfo is None:
                window_start = window_start.replace(tzinfo=timezone.utc)

            incident = touched.get(str(key)) or open_incidents.get(key)
            fresh = incident is not None and (
                window_start - self._as_utc(incident["last_seen_at"]) <= self.join_window
            )

            if incident is None or not fresh:
                incident = self._new_incident(anomaly, key, window_start)
                stats["incidents_created"] += 1
                open_incidents[key] = incident
            else:
                stats["incidents_updated"] += 1

            self._merge(incident, anomaly, window_start)
            touched[str(key)] = incident
            assignments.append((anomaly["anomaly_id"], incident["incident_id"]))

        if touched:
            bulk_upsert(Collections.INCIDENTS, list(touched.values()), ["incident_id"], db=db)

        from pymongo import UpdateOne

        db[Collections.ANOMALIES].bulk_write(
            [
                UpdateOne({"anomaly_id": anomaly_id}, {"$set": {"incident_id": incident_id}})
                for anomaly_id, incident_id in assignments
            ],
            ordered=False,
        )

        stats["resolved"] = self.auto_resolve(db)
        if stats["incidents_created"] or stats["incidents_updated"]:
            log.info(
                "correlated %d anomalies → %d new / %d updated incidents (%d auto-resolved)",
                stats["anomalies"], stats["incidents_created"], stats["incidents_updated"], stats["resolved"],
            )
        return stats

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def _new_incident(self, anomaly: Dict[str, Any], key: Tuple[str, str, str], started_at: datetime) -> Dict[str, Any]:
        family, entity_type, entity = key
        return {
            "incident_id": _incident_id(key, started_at),
            "title": _title(anomaly, key),
            "family": family,
            "family_label": FAMILY_LABEL.get(family, family.title()),
            "type": anomaly["type"],
            "entity_type": entity_type,
            "entity": entity,
            "severity": anomaly["severity"],
            "score": anomaly["score"],
            "peak_score": anomaly["score"],
            "status": "open",
            "started_at": started_at,
            "last_seen_at": started_at,
            "anomaly_count": 0,
            "anomaly_ids": [],
            "detectors": [],
            "types": [],
            "entities": [],
            "summary": anomaly.get("reason", ""),
            "timeline": [],
            "impact": {},
            "ground_truth": anomaly.get("ground_truth"),
            "created_at": datetime.now(timezone.utc),
        }

    def _merge(self, incident: Dict[str, Any], anomaly: Dict[str, Any], window_start: datetime) -> None:
        incident["anomaly_count"] += 1
        incident["anomaly_ids"] = (incident.get("anomaly_ids", []) + [anomaly["anomaly_id"]])[-500:]
        incident["last_seen_at"] = max(self._as_utc(incident["last_seen_at"]), window_start)
        incident["peak_score"] = max(incident.get("peak_score", 0), anomaly["score"])
        incident["score"] = round(
            0.7 * incident["peak_score"] + 0.3 * (incident.get("score", 0) or anomaly["score"]), 2
        )

        if Severity.ORDER[anomaly["severity"]] > Severity.ORDER[incident["severity"]]:
            incident["severity"] = anomaly["severity"]
            incident["title"] = _title(anomaly, (incident["family"], incident["entity_type"], incident["entity"]))
            incident["summary"] = anomaly.get("reason", incident.get("summary", ""))

        for field, value in (("detectors", anomaly.get("detector")), ("types", anomaly.get("type"))):
            if value and value not in incident.get(field, []):
                incident.setdefault(field, []).append(value)

        subject = anomaly.get("metrics", {}).get("ip") or anomaly.get("entity")
        if subject and subject not in incident.get("entities", []) and len(incident.get("entities", [])) < 50:
            incident.setdefault("entities", []).append(subject)

        # Keep a compact, human-readable timeline: one entry per minute at most.
        timeline = incident.setdefault("timeline", [])
        if not timeline or self._as_utc(timeline[-1]["at"]) != window_start:
            timeline.append(
                {
                    "at": window_start,
                    "type": anomaly["type"],
                    "severity": anomaly["severity"],
                    "score": anomaly["score"],
                    "entity": anomaly["entity"],
                    "reason": anomaly.get("headline") or anomaly.get("reason", "")[:220],
                }
            )
            incident["timeline"] = timeline[-120:]

        metrics = anomaly.get("metrics", {}) or {}
        impact = incident.setdefault("impact", {})
        impact["requests"] = impact.get("requests", 0) + int(metrics.get("requests", 0) or 0)
        impact["peak_error_rate"] = max(impact.get("peak_error_rate", 0.0), float(metrics.get("error_rate", 0) or 0))
        impact["peak_p95_ms"] = max(impact.get("peak_p95_ms", 0.0), float(metrics.get("p95_response_time", 0) or 0))
        impact["peak_rps"] = max(impact.get("peak_rps", 0.0), float(metrics.get("rps", 0) or 0))
        impact["duration_minutes"] = round(
            (self._as_utc(incident["last_seen_at"]) - self._as_utc(incident["started_at"])).total_seconds() / 60 + 1, 1
        )
        if anomaly.get("ground_truth") and not incident.get("ground_truth"):
            incident["ground_truth"] = anomaly["ground_truth"]

    def auto_resolve(self, db) -> int:
        cutoff = datetime.now(timezone.utc) - self.resolve_after
        result = db[Collections.INCIDENTS].update_many(
            {"status": "open", "last_seen_at": {"$lt": cutoff}},
            {"$set": {"status": "resolved", "resolved_at": datetime.now(timezone.utc), "resolution": "auto"}},
        )
        return result.modified_count
