"""SparkSession construction and tuning for the streaming application."""

from __future__ import annotations

import logging
import os

from loglens_common.config import settings

log = logging.getLogger("loglens.streaming.session")

KAFKA_PACKAGE_DEFAULT = "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1"


def build_spark(app_name: str | None = None):
    """Create the session with settings tuned for many small stateful queries.

    Notable choices:
      * ``shuffle.partitions`` is small — with 1-minute windows on a laptop-sized
        cluster, hundreds of partitions would mean pure scheduling overhead.
      * ``RocksDBStateStoreProvider`` keeps the state of eight concurrent
        stateful queries off the JVM heap.
      * Adaptive query execution is left on for the batch side of foreachBatch.
    """
    from pyspark.sql import SparkSession

    packages = os.getenv("SPARK_KAFKA_PACKAGE", KAFKA_PACKAGE_DEFAULT)

    builder = (
        SparkSession.builder.appName(app_name or settings.spark.app_name)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.shuffle.partitions", str(settings.spark.shuffle_partitions))
        .config("spark.sql.streaming.stateStore.providerClass",
                "org.apache.spark.sql.execution.streaming.state.RocksDBStateStoreProvider")
        .config("spark.sql.streaming.metricsEnabled", "true")
        .config("spark.sql.streaming.checkpointLocation", settings.spark.checkpoint_dir)
        .config("spark.sql.adaptive.enabled", "true")
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .config("spark.sql.execution.arrow.pyspark.fallback.enabled", "true")
        .config("spark.streaming.stopGracefullyOnShutdown", "true")
        .config("spark.executor.memory", settings.spark.executor_memory)
        .config("spark.driver.memory", settings.spark.driver_memory)
        .config("spark.executor.cores", str(settings.spark.executor_cores))
        .config("spark.driver.maxResultSize", "1g")
        .config("spark.jars.packages", packages)
        .config("spark.jars.ivy", os.getenv("SPARK_IVY_DIR", "/opt/loglens/ivy"))
    )

    driver_host = os.getenv("SPARK_DRIVER_HOST")
    if driver_host:
        # In a container the driver must advertise a resolvable hostname while
        # still binding to every interface.
        builder = builder.config("spark.driver.host", driver_host).config(
            "spark.driver.bindAddress", "0.0.0.0"
        )

    master = settings.spark.master
    if master:
        builder = builder.master(master)

    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel(os.getenv("SPARK_LOG_LEVEL", "WARN"))
    log.info(
        "spark %s ready — master=%s shuffle.partitions=%s checkpoint=%s",
        spark.version, master, settings.spark.shuffle_partitions, settings.spark.checkpoint_dir,
    )
    return spark
