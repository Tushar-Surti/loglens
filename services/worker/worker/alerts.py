"""Alert-rule evaluation.

Rules are documents, not code, so they can be edited from the Settings page.
Each rule matches on anomaly type and minimum severity and carries a cooldown
so a five-minute incident yields one page, not three hundred.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loglens_common.mongo import get_db
from loglens_common.schemas import Collections, DEFAULT_ALERT_RULES, Severity

log = logging.getLogger("loglens.worker.alerts")

WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "").strip()
WEBHOOK_TIMEOUT = float(os.getenv("ALERT_WEBHOOK_TIMEOUT", "4"))


def ensure_default_rules(db=None) -> int:
    """Seed the built-in rules once; never overwrite user edits afterwards."""
    db = get_db() if db is None else db
    created = 0
    for rule in DEFAULT_ALERT_RULES:
        result = db[Collections.ALERT_RULES].update_one(
            {"rule_id": rule["rule_id"]},
            {"$setOnInsert": {**rule, "created_at": datetime.now(timezone.utc), "builtin": True}},
            upsert=True,
        )
        created += 1 if result.upserted_id else 0
    if created:
        log.info("seeded %d built-in alert rules", created)
    return created


def _matches(rule: Dict[str, Any], anomaly: Dict[str, Any]) -> bool:
    match = rule.get("match", {}) or {}
    minimum = match.get("severity_at_least")
    if minimum and Severity.ORDER.get(anomaly.get("severity"), 0) < Severity.ORDER.get(minimum, 0):
        return False
    types = match.get("types")
    if types and anomaly.get("type") not in types:
        return False
    entity_types = match.get("entity_types")
    if entity_types and anomaly.get("entity_type") not in entity_types:
        return False
    min_score = match.get("min_score")
    if min_score is not None and float(anomaly.get("score", 0)) < float(min_score):
        return False
    return True


def _alert_id(rule_id: str, anomaly: Dict[str, Any]) -> str:
    digest = hashlib.sha1(
        f"{rule_id}|{anomaly.get('incident_id') or anomaly['entity']}|{anomaly['window_start']}".encode("utf-8")
    ).hexdigest()
    return f"alt_{digest[:18]}"


def _dispatch_webhook(alert: Dict[str, Any]) -> str:
    if not WEBHOOK_URL:
        return "skipped"
    payload = json.dumps(
        {
            "alert_id": alert["alert_id"],
            "rule": alert["rule_name"],
            "severity": alert["severity"],
            "title": alert["title"],
            "message": alert["message"],
            "entity": alert["entity"],
            "score": alert["score"],
            "at": alert["created_at"].isoformat(),
        },
        default=str,
    ).encode("utf-8")
    request = urllib.request.Request(
        WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT) as response:
            return f"delivered ({response.status})"
    except (urllib.error.URLError, TimeoutError) as exc:  # pragma: no cover - network dependent
        log.warning("webhook delivery failed: %s", exc)
        return f"failed ({exc})"


class AlertEngine:
    def __init__(self, lookback_minutes: int = 20):
        self.lookback = timedelta(minutes=lookback_minutes)

    def run(self, db=None) -> Dict[str, int]:
        db = get_db() if db is None else db
        rules = list(db[Collections.ALERT_RULES].find({"enabled": True}))
        if not rules:
            ensure_default_rules(db)
            rules = list(db[Collections.ALERT_RULES].find({"enabled": True}))

        since = datetime.now(timezone.utc) - self.lookback
        anomalies = list(
            db[Collections.ANOMALIES]
            .find({"detected_at": {"$gte": since}, "alerted": {"$ne": True}})
            .sort("score", -1)
            .limit(500)
        )
        stats = {"evaluated": len(anomalies), "created": 0, "suppressed": 0}
        if not anomalies:
            return stats

        alerted_ids: List[str] = []
        for anomaly in anomalies:
            for rule in rules:
                if not _matches(rule, anomaly):
                    continue
                alert_id = _alert_id(rule["rule_id"], anomaly)
                if db[Collections.ALERTS].find_one({"alert_id": alert_id}, {"_id": 1}):
                    stats["suppressed"] += 1
                    continue

                cooldown = int(rule.get("cooldown_seconds", 120))
                recent = db[Collections.ALERTS].find_one(
                    {
                        "rule_id": rule["rule_id"],
                        "entity": anomaly["entity"],
                        "created_at": {"$gte": datetime.now(timezone.utc) - timedelta(seconds=cooldown)},
                    },
                    {"_id": 1},
                )
                if recent:
                    stats["suppressed"] += 1
                    continue

                alert = {
                    "alert_id": alert_id,
                    "rule_id": rule["rule_id"],
                    "rule_name": rule.get("name", rule["rule_id"]),
                    "severity": anomaly["severity"],
                    "score": anomaly["score"],
                    "title": anomaly.get("headline") or anomaly.get("reason", "")[:140],
                    "message": anomaly.get("reason", ""),
                    "type": anomaly["type"],
                    "entity": anomaly["entity"],
                    "entity_type": anomaly["entity_type"],
                    "anomaly_id": anomaly["anomaly_id"],
                    "incident_id": anomaly.get("incident_id"),
                    "channels": rule.get("channels", ["dashboard"]),
                    "created_at": datetime.now(timezone.utc),
                    "window_start": anomaly["window_start"],
                    "status": "firing",
                    "acknowledged": False,
                }
                if "webhook" in alert["channels"]:
                    alert["webhook_status"] = _dispatch_webhook(alert)

                db[Collections.ALERTS].insert_one(alert)
                stats["created"] += 1
                log.warning("ALERT [%s] %s — %s", alert["severity"].upper(), alert["rule_name"], alert["title"])
            alerted_ids.append(anomaly["anomaly_id"])

        if alerted_ids:
            db[Collections.ANOMALIES].update_many(
                {"anomaly_id": {"$in": alerted_ids}}, {"$set": {"alerted": True}}
            )
        return stats
