"""Canonical data contracts shared across the whole pipeline.

The log event schema is defined once here and reused by:
  * the generator (to emit valid JSON),
  * Spark (``spark_log_schema()`` builds the equivalent ``StructType``),
  * the API (response typing / validation),
  * the ML pipeline (feature extraction).

Keeping one definition prevents the classic streaming-project failure mode where
producer and consumer drift apart.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1


# ── Collections ──────────────────────────────────────────────────────────────
class Collections:
    RAW_LOGS = "raw_logs"
    METRICS_GLOBAL = "metrics_global"
    METRICS_ENDPOINT = "metrics_endpoint"
    METRICS_IP = "metrics_ip"
    METRICS_STATUS = "metrics_status"
    METRICS_GEO = "metrics_geo"
    METRICS_SERVICE = "metrics_service"
    SESSIONS = "sessions"
    ANOMALIES = "anomalies"
    INCIDENTS = "incidents"
    ALERTS = "alerts"
    ALERT_RULES = "alert_rules"
    CONFIG = "config"
    SYSTEM_HEALTH = "system_health"
    PIPELINE_STATS = "pipeline_stats"
    MODELS = "models"
    BASELINES = "baselines"


ALL_COLLECTIONS = [
    getattr(Collections, name) for name in dir(Collections) if not name.startswith("_")
]


# ── Enumerations ─────────────────────────────────────────────────────────────
class Severity:
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    ORDER = {INFO: 0, LOW: 1, MEDIUM: 2, HIGH: 3, CRITICAL: 4}

    @classmethod
    def from_score(cls, score: float, thresholds: Optional[Dict[str, float]] = None) -> str:
        t = thresholds or {}
        if score >= t.get("severity_critical", 85):
            return cls.CRITICAL
        if score >= t.get("severity_high", 70):
            return cls.HIGH
        if score >= t.get("severity_medium", 50):
            return cls.MEDIUM
        if score >= t.get("severity_low", 30):
            return cls.LOW
        return cls.INFO


class AnomalyType:
    TRAFFIC_SPIKE = "traffic_spike"
    TRAFFIC_DROP = "traffic_drop"
    DDOS_BURST = "ddos_burst"
    ERROR_SPIKE = "error_spike"
    LATENCY_ANOMALY = "latency_anomaly"
    SLOW_ENDPOINT = "slow_endpoint"
    SUSPICIOUS_IP = "suspicious_ip"
    ENDPOINT_SCAN = "endpoint_scan"
    AUTH_ABUSE = "auth_abuse"
    DATA_SCRAPING = "data_scraping"
    BOT_SURGE = "bot_surge"
    GEO_ANOMALY = "geo_anomaly"
    MULTIVARIATE = "multivariate_anomaly"
    SESSION_ANOMALY = "session_anomaly"

    LABELS = {
        TRAFFIC_SPIKE: "Traffic spike",
        TRAFFIC_DROP: "Traffic drop",
        DDOS_BURST: "DDoS-like burst",
        ERROR_SPIKE: "Error-rate spike",
        LATENCY_ANOMALY: "Latency anomaly",
        SLOW_ENDPOINT: "Slow endpoint",
        SUSPICIOUS_IP: "Suspicious IP behaviour",
        ENDPOINT_SCAN: "Endpoint scanning",
        AUTH_ABUSE: "Authentication abuse",
        DATA_SCRAPING: "Data scraping",
        BOT_SURGE: "Bot surge",
        GEO_ANOMALY: "Geographic anomaly",
        MULTIVARIATE: "Multivariate anomaly",
        SESSION_ANOMALY: "Abnormal session behaviour",
    }


class EntityType:
    GLOBAL = "global"
    ENDPOINT = "endpoint"
    IP = "ip"
    SESSION = "session"
    COUNTRY = "country"
    SERVICE = "service"


class Detector:
    ROBUST_Z = "robust_zscore"
    EWMA = "ewma"
    SEASONAL = "seasonal_baseline"
    CUSUM = "cusum_changepoint"
    TREND = "trend_residual"
    ISOLATION_FOREST = "isolation_forest"
    MAHALANOBIS = "mahalanobis"
    ENTROPY = "path_entropy"
    RULE = "rule_engine"
    DBSCAN = "dbscan_cluster"


class AnomalyStatus:
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


# ── Log event ────────────────────────────────────────────────────────────────
LOG_FIELDS: List[tuple] = [
    # (name, json type, spark type)
    ("event_id", "string", "string"),
    ("schema_version", "int", "int"),
    ("timestamp", "string", "string"),
    ("ingest_ts", "string", "string"),
    ("service", "string", "string"),
    ("region", "string", "string"),
    ("host", "string", "string"),
    ("ip", "string", "string"),
    ("method", "string", "string"),
    ("path", "string", "string"),
    ("endpoint", "string", "string"),
    ("query", "string", "string"),
    ("protocol", "string", "string"),
    ("status", "int", "int"),
    ("bytes_sent", "long", "long"),
    ("bytes_received", "long", "long"),
    ("response_time_ms", "double", "double"),
    ("upstream_time_ms", "double", "double"),
    ("referrer", "string", "string"),
    ("user_agent", "string", "string"),
    ("user_id", "string", "string"),
    ("session_id", "string", "string"),
    ("request_id", "string", "string"),
    ("country", "string", "string"),
    ("country_name", "string", "string"),
    ("city", "string", "string"),
    ("lat", "double", "double"),
    ("lon", "double", "double"),
    ("asn", "string", "string"),
    ("org", "string", "string"),
    ("device", "string", "string"),
    ("browser", "string", "string"),
    ("os", "string", "string"),
    ("is_bot", "boolean", "boolean"),
    ("cache_status", "string", "string"),
    ("tls_version", "string", "string"),
    ("attack_label", "string", "string"),
]


@dataclass
class LogEvent:
    """One HTTP access-log line, fully enriched at the edge."""

    event_id: str
    timestamp: str
    ip: str
    method: str
    path: str
    endpoint: str
    status: int
    response_time_ms: float
    bytes_sent: int = 0
    bytes_received: int = 0
    upstream_time_ms: float = 0.0
    schema_version: int = SCHEMA_VERSION
    ingest_ts: str = ""
    service: str = "web-storefront"
    region: str = "us-east-1"
    host: str = "web-01"
    query: str = ""
    protocol: str = "HTTP/1.1"
    referrer: str = "-"
    user_agent: str = "-"
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: str = ""
    country: str = "US"
    country_name: str = "United States"
    city: str = "Ashburn"
    lat: float = 39.04
    lon: float = -77.49
    asn: str = "AS0"
    org: str = "unknown"
    device: str = "desktop"
    browser: str = "Chrome"
    os: str = "Windows"
    is_bot: bool = False
    cache_status: str = "MISS"
    tls_version: str = "TLSv1.3"
    attack_label: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def spark_log_schema():
    """Build the equivalent PySpark ``StructType`` (imported lazily)."""
    from pyspark.sql.types import (
        BooleanType,
        DoubleType,
        IntegerType,
        LongType,
        StringType,
        StructField,
        StructType,
    )

    mapping = {
        "string": StringType(),
        "int": IntegerType(),
        "long": LongType(),
        "double": DoubleType(),
        "boolean": BooleanType(),
    }
    return StructType(
        [StructField(name, mapping[spark_type], True) for name, _, spark_type in LOG_FIELDS]
    )


# ── Anomaly ──────────────────────────────────────────────────────────────────
@dataclass
class Anomaly:
    """A single detection emitted by one detector for one entity/window."""

    anomaly_id: str
    type: str
    entity_type: str
    entity: str
    score: float
    severity: str
    detector: str
    reason: str
    window_start: datetime
    window_end: datetime
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    evidence: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    contributing: List[Dict[str, Any]] = field(default_factory=list)
    status: str = AnomalyStatus.OPEN
    incident_id: Optional[str] = None
    ground_truth: Optional[str] = None
    service: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Incident:
    """A correlated group of anomalies that belong to the same real event."""

    incident_id: str
    title: str
    type: str
    entity_type: str
    entity: str
    severity: str
    score: float
    status: str
    started_at: datetime
    last_seen_at: datetime
    anomaly_count: int = 0
    anomaly_ids: List[str] = field(default_factory=list)
    detectors: List[str] = field(default_factory=list)
    summary: str = ""
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    impact: Dict[str, Any] = field(default_factory=dict)
    resolved_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_ALERT_RULES: List[Dict[str, Any]] = [
    {
        "rule_id": "rule-critical-any",
        "name": "Any critical anomaly",
        "description": "Page on-call for every critical-severity detection.",
        "enabled": True,
        "match": {"severity_at_least": Severity.CRITICAL},
        "channels": ["dashboard", "webhook"],
        "cooldown_seconds": 120,
    },
    {
        "rule_id": "rule-error-spike",
        "name": "Error-rate spike (high)",
        "description": "5xx rate departs from its seasonal baseline.",
        "enabled": True,
        "match": {"types": [AnomalyType.ERROR_SPIKE], "severity_at_least": Severity.HIGH},
        "channels": ["dashboard"],
        "cooldown_seconds": 180,
    },
    {
        "rule_id": "rule-ddos",
        "name": "DDoS-like burst",
        "description": "Request burst concentrated in few source IPs.",
        "enabled": True,
        "match": {
            "types": [AnomalyType.DDOS_BURST, AnomalyType.SUSPICIOUS_IP],
            "severity_at_least": Severity.HIGH,
        },
        "channels": ["dashboard", "webhook"],
        "cooldown_seconds": 60,
    },
    {
        "rule_id": "rule-latency",
        "name": "Latency degradation",
        "description": "p95 latency above the learned envelope.",
        "enabled": True,
        "match": {
            "types": [AnomalyType.LATENCY_ANOMALY, AnomalyType.SLOW_ENDPOINT],
            "severity_at_least": Severity.MEDIUM,
        },
        "channels": ["dashboard"],
        "cooldown_seconds": 300,
    },
    {
        "rule_id": "rule-auth-abuse",
        "name": "Credential stuffing",
        "description": "Repeated authentication failures from a small IP set.",
        "enabled": True,
        "match": {
            "types": [AnomalyType.AUTH_ABUSE, AnomalyType.ENDPOINT_SCAN],
            "severity_at_least": Severity.MEDIUM,
        },
        "channels": ["dashboard", "webhook"],
        "cooldown_seconds": 120,
    },
]
