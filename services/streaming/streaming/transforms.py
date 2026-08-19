"""Stream parsing, enrichment and the windowed aggregations.

All Spark-side data shaping lives here so ``main.py`` reads as a topology
description and the transformations stay independently testable against a
static DataFrame.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F

from loglens_common.config import settings
from loglens_common.schemas import spark_log_schema

APDEX_TARGET_MS = 500.0


# ── Ingest ───────────────────────────────────────────────────────────────────
def read_kafka(spark: SparkSession, topic: str, starting_offsets: str = "latest") -> DataFrame:
    return (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", settings.kafka.bootstrap_servers)
        .option("subscribe", topic)
        .option("startingOffsets", starting_offsets)
        .option("maxOffsetsPerTrigger", str(settings.spark.max_offsets_per_trigger))
        # A local demo restarts often; losing a compacted-away offset must not
        # kill the pipeline.
        .option("failOnDataLoss", "false")
        .option("kafka.group.id", "loglens-spark")
        .load()
    )


def parse_events(raw: DataFrame) -> DataFrame:
    """Decode JSON, drop unparseable records, attach the event-time watermark."""
    schema = spark_log_schema()
    parsed = raw.select(
        F.col("timestamp").alias("kafka_ts"),
        F.col("partition").alias("kafka_partition"),
        F.col("offset").alias("kafka_offset"),
        F.from_json(F.col("value").cast("string"), schema).alias("payload"),
    )
    return (
        parsed.select("kafka_ts", "kafka_partition", "kafka_offset", "payload.*")
        .withColumn("event_time", F.to_timestamp(F.col("timestamp")))
        .filter(F.col("event_time").isNotNull())
        .filter(F.col("status").isNotNull() & (F.col("status") > 0))
        .filter(F.col("endpoint").isNotNull())
    )


def enrich(events: DataFrame) -> DataFrame:
    """Derive the boolean/categorical columns every aggregation depends on."""
    return (
        events.withColumn("status_class", F.concat(F.floor(F.col("status") / 100).cast("string"), F.lit("xx")))
        .withColumn("is_client_error", (F.col("status") >= 400) & (F.col("status") < 500))
        .withColumn("is_server_error", F.col("status") >= 500)
        .withColumn("is_error", F.col("status") >= 400)
        .withColumn("is_not_found", F.col("status") == 404)
        .withColumn(
            "is_auth_fail",
            F.col("status").isin(401, 403) & F.col("path").contains("/auth/"),
        )
        .withColumn("cache_hit", F.col("cache_status") == F.lit("HIT"))
        .withColumn("is_slow", F.col("response_time_ms") > F.lit(APDEX_TARGET_MS))
        .withColumn(
            "apdex_weight",
            F.when(F.col("response_time_ms") <= APDEX_TARGET_MS, F.lit(1.0))
            .when(F.col("response_time_ms") <= APDEX_TARGET_MS * 4, F.lit(0.5))
            .otherwise(F.lit(0.0)),
        )
        .withColumn("ingest_latency_ms",
                    (F.unix_timestamp("kafka_ts") - F.unix_timestamp("event_time")) * 1000)
        .withColumn("is_bot", F.coalesce(F.col("is_bot"), F.lit(False)))
        .withColumn("bytes_sent", F.coalesce(F.col("bytes_sent"), F.lit(0)))
        .withColumn("response_time_ms", F.coalesce(F.col("response_time_ms"), F.lit(0.0)))
    )


def with_watermark(events: DataFrame, delay: str | None = None) -> DataFrame:
    return events.withWatermark("event_time", delay or settings.spark.watermark_delay)


# ── Shared aggregate expressions ─────────────────────────────────────────────
def _latency_percentiles() -> Column:
    # percentile_approx keeps a bounded-error digest in the state store, which is
    # what makes per-window percentiles affordable on an unbounded stream.
    return F.expr("percentile_approx(response_time_ms, array(0.5, 0.9, 0.95, 0.99), 200)").alias("pcts")


def _common_aggs() -> list:
    return [
        F.count(F.lit(1)).alias("requests"),
        F.sum(F.col("is_client_error").cast("int")).alias("errors_4xx"),
        F.sum(F.col("is_server_error").cast("int")).alias("errors_5xx"),
        F.sum(F.col("bytes_sent")).alias("bytes_sent"),
        F.avg("response_time_ms").alias("avg_response_time"),
        F.max("response_time_ms").alias("max_response_time"),
        F.avg("apdex_weight").alias("apdex"),
        _latency_percentiles(),
    ]


def _explode_percentiles(df: DataFrame) -> DataFrame:
    return (
        df.withColumn("p50_response_time", F.col("pcts")[0])
        .withColumn("p90_response_time", F.col("pcts")[1])
        .withColumn("p95_response_time", F.col("pcts")[2])
        .withColumn("p99_response_time", F.col("pcts")[3])
        .drop("pcts")
    )


def _window_columns(df: DataFrame) -> DataFrame:
    return df.withColumn("window_start", F.col("window.start")).withColumn(
        "window_end", F.col("window.end")
    ).drop("window")


# ── Aggregations ─────────────────────────────────────────────────────────────
#: Status codes broken out individually.  Bounded conditional sums keep the
#: per-window state tiny; ``collect_list`` would buffer every event in the
#: window and blow up the state store.
TRACKED_STATUSES = [200, 201, 204, 206, 301, 302, 304, 400, 401, 403, 404, 409, 422, 429, 500, 502, 503, 504]
TRACKED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def _status_breakdown() -> list:
    return [
        F.sum((F.col("status") == code).cast("int")).alias(f"status_{code}") for code in TRACKED_STATUSES
    ] + [
        F.sum((F.col("method") == method).cast("int")).alias(f"method_{method}") for method in TRACKED_METHODS
    ]


def global_metrics(events: DataFrame, window: str | None = None) -> DataFrame:
    window = window or settings.spark.window_metric
    aggregated = events.groupBy(F.window("event_time", window)).agg(
        *_common_aggs(),
        *_status_breakdown(),
        F.approx_count_distinct("ip", 0.02).alias("unique_ips"),
        F.approx_count_distinct("session_id", 0.02).alias("unique_sessions"),
        F.approx_count_distinct("user_id", 0.02).alias("unique_users"),
        F.approx_count_distinct("endpoint").alias("unique_endpoints"),
        F.sum(F.col("is_bot").cast("int")).alias("bot_requests"),
        F.sum(F.col("cache_hit").cast("int")).alias("cache_hits"),
        F.avg("ingest_latency_ms").alias("avg_ingest_latency_ms"),
        F.max("attack_label").alias("dominant_attack_label"),
    )
    return _explode_percentiles(_window_columns(aggregated))


def endpoint_metrics(events: DataFrame, window: str | None = None) -> DataFrame:
    window = window or settings.spark.window_metric
    aggregated = events.groupBy(
        F.window("event_time", window), F.col("endpoint"), F.col("method"), F.col("service")
    ).agg(
        *_common_aggs(),
        F.approx_count_distinct("ip", 0.05).alias("unique_ips"),
        F.approx_count_distinct("session_id", 0.05).alias("unique_sessions"),
        F.sum(F.col("cache_hit").cast("int")).alias("cache_hits"),
        F.max("attack_label").alias("dominant_attack_label"),
    )
    return _explode_percentiles(_window_columns(aggregated))


def ip_endpoint_metrics(events: DataFrame, window: str | None = None) -> DataFrame:
    """Per (window, ip, endpoint) — rolled up to per-IP features in the sink.

    Grouping on the normalised endpoint rather than the raw path is deliberate:
    dynamic ids would make every request a "unique path" and destroy the
    scanning signal, while scanner probes each become their own endpoint.
    """
    window = window or settings.spark.window_metric
    aggregated = events.groupBy(
        F.window("event_time", window), F.col("ip"), F.col("endpoint")
    ).agg(
        F.count(F.lit(1)).alias("requests"),
        F.sum(F.col("is_error").cast("int")).alias("errors"),
        F.sum(F.col("is_not_found").cast("int")).alias("not_found"),
        F.sum(F.col("is_server_error").cast("int")).alias("server_errors"),
        F.sum(F.col("is_auth_fail").cast("int")).alias("auth_fails"),
        F.sum(F.col("bytes_sent")).alias("bytes_sent"),
        F.avg("response_time_ms").alias("avg_response_time"),
        F.max("response_time_ms").alias("max_response_time"),
        F.approx_count_distinct("user_agent").alias("user_agents"),
        F.approx_count_distinct("session_id").alias("sessions"),
        F.min("event_time").alias("first_seen"),
        F.max("event_time").alias("last_seen"),
        F.avg(F.col("is_bot").cast("int")).alias("bot_ratio"),
        F.first("country", ignorenulls=True).alias("country"),
        F.first("asn", ignorenulls=True).alias("asn"),
        F.first("org", ignorenulls=True).alias("org"),
        F.max("attack_label").alias("attack_label"),
    )
    return _window_columns(aggregated)


def geo_metrics(events: DataFrame, window: str | None = None) -> DataFrame:
    window = window or settings.spark.window_geo
    aggregated = events.groupBy(F.window("event_time", window), F.col("country")).agg(
        F.count(F.lit(1)).alias("requests"),
        F.approx_count_distinct("ip", 0.05).alias("unique_ips"),
        F.approx_count_distinct("session_id", 0.05).alias("unique_sessions"),
        F.avg(F.col("is_error").cast("int")).alias("error_rate"),
        F.avg("response_time_ms").alias("avg_response_time"),
        F.expr("percentile_approx(response_time_ms, 0.95, 200)").alias("p95_response_time"),
        F.sum("bytes_sent").alias("bytes_sent"),
        F.first("country_name", ignorenulls=True).alias("country_name"),
        F.avg("lat").alias("lat"),
        F.avg("lon").alias("lon"),
        F.max("attack_label").alias("dominant_attack_label"),
    )
    return _window_columns(aggregated)


def service_metrics(events: DataFrame, window: str | None = None) -> DataFrame:
    window = window or settings.spark.window_metric
    aggregated = events.groupBy(F.window("event_time", window), F.col("service")).agg(
        F.count(F.lit(1)).alias("requests"),
        F.avg(F.col("is_error").cast("int")).alias("error_rate"),
        F.avg(F.col("is_server_error").cast("int")).alias("server_error_rate"),
        F.avg("response_time_ms").alias("avg_response_time"),
        F.expr("percentile_approx(response_time_ms, array(0.95, 0.99), 200)").alias("pcts"),
        F.avg("apdex_weight").alias("apdex"),
        F.approx_count_distinct("host").alias("hosts"),
    )
    return (
        _window_columns(aggregated)
        .withColumn("p95_response_time", F.col("pcts")[0])
        .withColumn("p99_response_time", F.col("pcts")[1])
        .drop("pcts")
    )


def session_metrics(events: DataFrame, gap: str | None = None) -> DataFrame:
    """Session windows: a genuine gap-based sessionisation, not a fixed bucket.

    Emitted in append mode once the inactivity gap closes, which is exactly the
    semantics a real user session has.
    """
    gap = gap or settings.spark.session_gap
    aggregated = events.groupBy(
        F.session_window("event_time", gap), F.col("session_id")
    ).agg(
        F.count(F.lit(1)).alias("requests"),
        F.min("event_time").alias("session_start"),
        F.max("event_time").alias("session_end"),
        F.approx_count_distinct("endpoint").alias("unique_endpoints"),
        F.sum(F.col("is_error").cast("int")).alias("error_count"),
        F.sum("bytes_sent").alias("bytes_sent"),
        F.avg("response_time_ms").alias("avg_response_time"),
        F.first("user_id", ignorenulls=True).alias("user_id"),
        F.first("ip", ignorenulls=True).alias("ip"),
        F.first("country", ignorenulls=True).alias("country"),
        F.first("device", ignorenulls=True).alias("device"),
        F.first("browser", ignorenulls=True).alias("browser"),
        F.first("endpoint", ignorenulls=True).alias("entry_endpoint"),
        F.last("endpoint", ignorenulls=True).alias("exit_endpoint"),
        F.max(F.col("endpoint").rlike("checkout|payments").cast("int")).alias("converted_flag"),
        F.avg(F.col("is_bot").cast("int")).alias("bot_ratio"),
        F.max("attack_label").alias("dominant_attack_label"),
    )
    return (
        aggregated.withColumn("window_start", F.col("session_window.start"))
        .withColumn("window_end", F.col("session_window.end"))
        .drop("session_window")
        .withColumn(
            "duration_seconds",
            F.greatest(
                F.unix_timestamp("session_end") - F.unix_timestamp("session_start"), F.lit(1)
            ).cast("double"),
        )
        .withColumn("requests_per_minute", F.col("requests") / (F.col("duration_seconds") / F.lit(60.0)))
        .withColumn("converted", F.col("converted_flag") == 1)
        .withColumn("is_bot", F.col("bot_ratio") > 0.5)
        .drop("converted_flag")
    )


def enriched_for_kafka(events: DataFrame) -> DataFrame:
    """Re-serialise the enriched stream for downstream consumers (live tail)."""
    payload_columns = [
        "event_id", "timestamp", "service", "host", "region", "ip", "method", "path", "endpoint",
        "status", "status_class", "bytes_sent", "response_time_ms", "upstream_time_ms",
        "user_id", "session_id", "country", "country_name", "city", "asn", "org",
        "device", "browser", "os", "is_bot", "cache_status", "user_agent", "referrer",
        "is_error", "is_server_error", "attack_label",
    ]
    return events.select(
        F.col("ip").cast("string").alias("key"),
        F.to_json(F.struct(*[F.col(c) for c in payload_columns])).alias("value"),
    )
