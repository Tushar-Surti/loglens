"""Environment-driven configuration.

A single frozen ``Settings`` object is built at import time from the process
environment.  Deliberately plain-stdlib: this module is imported inside Spark
executors, where extra dependencies are a liability.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import List


def _str(key: str, default: str) -> str:
    value = os.getenv(key)
    return default if value is None or value == "" else value


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    return _str(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _list(key: str, default: str) -> List[str]:
    return [item.strip() for item in _str(key, default).split(",") if item.strip()]


@dataclass(frozen=True)
class KafkaSettings:
    bootstrap_servers: str = field(
        default_factory=lambda: _str("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
    )
    topic_raw: str = field(default_factory=lambda: _str("KAFKA_TOPIC_RAW", "weblogs.raw"))
    topic_enriched: str = field(default_factory=lambda: _str("KAFKA_TOPIC_ENRICHED", "weblogs.enriched"))
    topic_anomalies: str = field(default_factory=lambda: _str("KAFKA_TOPIC_ANOMALIES", "weblogs.anomalies"))
    topic_alerts: str = field(default_factory=lambda: _str("KAFKA_TOPIC_ALERTS", "weblogs.alerts"))
    partitions: int = field(default_factory=lambda: _int("KAFKA_PARTITIONS", 6))
    replication_factor: int = field(default_factory=lambda: _int("KAFKA_REPLICATION_FACTOR", 1))
    linger_ms: int = field(default_factory=lambda: _int("KAFKA_PRODUCER_LINGER_MS", 25))
    batch_size: int = field(default_factory=lambda: _int("KAFKA_PRODUCER_BATCH_SIZE", 65536))
    compression: str = field(default_factory=lambda: _str("KAFKA_PRODUCER_COMPRESSION", "lz4"))


@dataclass(frozen=True)
class MongoSettings:
    uri: str = field(
        default_factory=lambda: _str("MONGO_URI", "mongodb://localhost:27017/?directConnection=true")
    )
    database: str = field(default_factory=lambda: _str("MONGO_DB", "loglens"))
    raw_ttl_seconds: int = field(default_factory=lambda: _int("MONGO_RAW_TTL_SECONDS", 86_400))
    metric_ttl_seconds: int = field(default_factory=lambda: _int("MONGO_METRIC_TTL_SECONDS", 1_209_600))


@dataclass(frozen=True)
class SparkSettings:
    master: str = field(default_factory=lambda: _str("SPARK_MASTER_URL", "local[*]"))
    app_name: str = field(default_factory=lambda: _str("SPARK_APP_NAME", "loglens-streaming"))
    shuffle_partitions: int = field(default_factory=lambda: _int("SPARK_SHUFFLE_PARTITIONS", 8))
    checkpoint_dir: str = field(default_factory=lambda: _str("SPARK_CHECKPOINT_DIR", "/tmp/loglens/checkpoints"))
    trigger_interval: str = field(default_factory=lambda: _str("SPARK_TRIGGER_INTERVAL", "10 seconds"))
    watermark_delay: str = field(default_factory=lambda: _str("SPARK_WATERMARK_DELAY", "2 minutes"))
    max_offsets_per_trigger: int = field(default_factory=lambda: _int("SPARK_MAX_OFFSETS_PER_TRIGGER", 200_000))
    executor_memory: str = field(default_factory=lambda: _str("SPARK_EXECUTOR_MEMORY", "2g"))
    driver_memory: str = field(default_factory=lambda: _str("SPARK_DRIVER_MEMORY", "2g"))
    executor_cores: int = field(default_factory=lambda: _int("SPARK_EXECUTOR_CORES", 2))
    window_metric: str = field(default_factory=lambda: _str("WINDOW_METRIC", "1 minute"))
    window_geo: str = field(default_factory=lambda: _str("WINDOW_GEO", "5 minutes"))
    session_gap: str = field(default_factory=lambda: _str("SESSION_GAP", "15 minutes"))


@dataclass(frozen=True)
class DetectionSettings:
    """Default detector tuning.

    These are *defaults*.  The running system reads live overrides from the
    ``config`` collection, so thresholds can be changed from the Settings page
    without a redeploy.
    """

    zscore_threshold: float = field(default_factory=lambda: _float("DET_ZSCORE_THRESHOLD", 3.5))
    ewma_alpha: float = field(default_factory=lambda: _float("DET_EWMA_ALPHA", 0.25))
    error_rate_threshold: float = field(default_factory=lambda: _float("DET_ERROR_RATE_THRESHOLD", 0.08))
    latency_p95_multiplier: float = field(default_factory=lambda: _float("DET_LATENCY_P95_MULTIPLIER", 2.5))
    ip_rps_threshold: float = field(default_factory=lambda: _float("DET_IP_RPS_THRESHOLD", 25))
    scan_unique_paths: int = field(default_factory=lambda: _int("DET_SCAN_UNIQUE_PATHS", 30))
    auth_fail_threshold: int = field(default_factory=lambda: _int("DET_AUTH_FAIL_THRESHOLD", 15))
    isoforest_contamination: float = field(default_factory=lambda: _float("DET_ISOFOREST_CONTAMINATION", 0.02))
    severity_critical: float = field(default_factory=lambda: _float("DET_SEVERITY_CRITICAL", 85))
    severity_high: float = field(default_factory=lambda: _float("DET_SEVERITY_HIGH", 70))
    severity_medium: float = field(default_factory=lambda: _float("DET_SEVERITY_MEDIUM", 50))
    severity_low: float = field(default_factory=lambda: _float("DET_SEVERITY_LOW", 30))
    min_history_points: int = field(default_factory=lambda: _int("DET_MIN_HISTORY_POINTS", 12))

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass(frozen=True)
class GeneratorSettings:
    base_rps: float = field(default_factory=lambda: _float("GEN_BASE_RPS", 180))
    seed: int = field(default_factory=lambda: _int("GEN_SEED", 1337))
    scenarios_enabled: bool = field(default_factory=lambda: _bool("GEN_SCENARIOS", True))
    services: List[str] = field(
        default_factory=lambda: _list(
            "GEN_SERVICES", "web-storefront,api-gateway,checkout-svc,search-svc,media-cdn"
        )
    )
    speed: float = field(default_factory=lambda: _float("GEN_SPEED", 1.0))


@dataclass(frozen=True)
class ApiSettings:
    host: str = field(default_factory=lambda: _str("API_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("API_PORT", 8000))
    cors_origins: List[str] = field(
        default_factory=lambda: _list(
            "API_CORS_ORIGINS", "http://localhost:5173,http://localhost:4173,http://localhost:3000"
        )
    )
    log_level: str = field(default_factory=lambda: _str("API_LOG_LEVEL", "INFO"))
    ws_tick_seconds: float = field(default_factory=lambda: _float("API_WS_TICK_SECONDS", 3))


@dataclass(frozen=True)
class WorkerSettings:
    correlation_interval_seconds: int = field(
        default_factory=lambda: _int("WORKER_CORRELATION_INTERVAL_SECONDS", 20)
    )
    incident_join_window_minutes: int = field(
        default_factory=lambda: _int("WORKER_INCIDENT_JOIN_WINDOW_MINUTES", 10)
    )
    health_interval_seconds: int = field(default_factory=lambda: _int("WORKER_HEALTH_INTERVAL_SECONDS", 15))


@dataclass(frozen=True)
class MlSettings:
    model_dir: str = field(default_factory=lambda: _str("MODEL_DIR", "/opt/loglens/models"))
    retrain_interval_minutes: int = field(default_factory=lambda: _int("MODEL_RETRAIN_INTERVAL_MINUTES", 60))
    min_training_rows: int = field(default_factory=lambda: _int("ML_MIN_TRAINING_ROWS", 240))


@dataclass(frozen=True)
class Settings:
    kafka: KafkaSettings = field(default_factory=KafkaSettings)
    mongo: MongoSettings = field(default_factory=MongoSettings)
    spark: SparkSettings = field(default_factory=SparkSettings)
    detection: DetectionSettings = field(default_factory=DetectionSettings)
    generator: GeneratorSettings = field(default_factory=GeneratorSettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    worker: WorkerSettings = field(default_factory=WorkerSettings)
    ml: MlSettings = field(default_factory=MlSettings)
    environment: str = field(default_factory=lambda: _str("LOGLENS_ENV", "local"))


settings = Settings()
