# Spark image.
#
# Built on python:3.11-slim with Spark installed from the PySpark distribution
# rather than on the official apache/spark image, which ships Python 3.8 —
# too old for the pinned numpy/pandas/scikit-learn used by the detectors.
# This way the driver, the executors and every other service run the identical
# Python and library versions, which is what keeps the models trained offline
# scoring correctly online.
#
# `/opt/spark` is symlinked to the distribution so the compose commands and the
# usual Spark paths work unchanged.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    SPARK_HOME=/opt/spark \
    SPARK_IVY_DIR=/opt/loglens/ivy \
    SPARK_KAFKA_PACKAGE=org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1 \
    PYSPARK_PYTHON=python3 \
    PYSPARK_DRIVER_PYTHON=python3

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless procps curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/loglens

COPY services/streaming/requirements.txt /tmp/req-streaming.txt
RUN pip install -r /tmp/req-streaming.txt \
    && ln -s /usr/local/lib/python3.11/site-packages/pyspark /opt/spark

ENV PATH="${SPARK_HOME}/bin:${SPARK_HOME}/sbin:${PATH}"

COPY services/common /opt/loglens/common
RUN pip install -e /opt/loglens/common

COPY services/streaming /opt/loglens/streaming
COPY services/ml /opt/loglens/ml

ENV PYTHONPATH=/opt/loglens/streaming:/opt/loglens/ml

# Resolve the Kafka connector and its transitive jars at build time into a baked
# Ivy cache, so the streaming job starts without a Maven round trip on every
# container start. Non-fatal: an offline build falls back to runtime resolution.
RUN mkdir -p ${SPARK_IVY_DIR} /opt/loglens/checkpoints /opt/loglens/models \
    && python -c "\
from pyspark.sql import SparkSession; \
import os; \
SparkSession.builder.master('local[1]') \
    .appName('ivy-warmup') \
    .config('spark.jars.packages', os.environ['SPARK_KAFKA_PACKAGE']) \
    .config('spark.jars.ivy', os.environ['SPARK_IVY_DIR']) \
    .config('spark.ui.enabled', 'false') \
    .getOrCreate().stop()" > /tmp/ivy-warm.log 2>&1 \
    || (echo 'ivy warm-up failed; jars will resolve at runtime:' && tail -20 /tmp/ivy-warm.log)

RUN chmod -R 777 /opt/loglens/checkpoints /opt/loglens/models ${SPARK_IVY_DIR}

CMD ["python", "-m", "streaming.main"]
