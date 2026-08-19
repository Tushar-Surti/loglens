# Shared base for the pure-Python services (generator, api, worker, ml).
# One image, four entrypoints — keeps local builds fast and layers cached.
FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/loglens

# Dependencies first so application edits do not invalidate the wheel cache.
COPY services/api/requirements.txt /tmp/req-api.txt
COPY services/generator/requirements.txt /tmp/req-generator.txt
COPY services/worker/requirements.txt /tmp/req-worker.txt
COPY services/ml/requirements.txt /tmp/req-ml.txt
RUN pip install -r /tmp/req-api.txt \
    && pip install -r /tmp/req-generator.txt \
    && pip install -r /tmp/req-worker.txt \
    && pip install -r /tmp/req-ml.txt

# Shared library, installed as a real package so imports work everywhere.
COPY services/common /opt/loglens/common
RUN pip install -e /opt/loglens/common

COPY services/api /opt/loglens/api
COPY services/generator /opt/loglens/generator
COPY services/worker /opt/loglens/worker
COPY services/ml /opt/loglens/ml

ENV PYTHONPATH=/opt/loglens/api:/opt/loglens/generator:/opt/loglens/worker:/opt/loglens/ml

RUN useradd --create-home --uid 10001 loglens \
    && mkdir -p /opt/loglens/models /opt/loglens/checkpoints \
    && chown -R loglens:loglens /opt/loglens
USER loglens

CMD ["python", "-c", "print('specify a command')"]
