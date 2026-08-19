"""LogLens API service.

FastAPI application exposing everything the dashboard needs: metrics, logs,
anomalies, incidents, alerts, geography, configuration and two WebSocket
streams.  All read paths hit MongoDB through Motor; no request ever blocks on
Kafka or Spark.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from loglens_common.config import settings
from loglens_common.logging_setup import setup_logging
from loglens_common.mongo import ensure_indexes, heartbeat

from .routers import explore, geo, overview, performance, security, system, ws

log = setup_logging("api", settings.api.log_level)

DESCRIPTION = """
Real-time web-log monitoring and anomaly detection.

**Pipeline** — log sources → Kafka → Spark Structured Streaming → statistical &
ML detection → MongoDB → this API → dashboard.

Every endpoint accepts a `range` (`15m`, `1h`, `24h`, `7d`) or an explicit
`start`/`end` pair.
"""


async def _heartbeat_loop() -> None:
    """Publish API liveness so the Health page can see this process."""
    while True:
        try:
            await asyncio.to_thread(
                heartbeat, "api", "healthy", {"connections": ws.registry.stats()}
            )
        except Exception as exc:  # pragma: no cover
            log.debug("heartbeat failed: %s", exc)
        await asyncio.sleep(20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting LogLens API (env=%s)", settings.environment)
    try:
        await asyncio.to_thread(ensure_indexes)
        log.info("mongo indexes verified")
    except Exception as exc:
        log.error("index bootstrap failed (continuing): %s", exc)

    task = asyncio.create_task(_heartbeat_loop())
    yield
    task.cancel()
    log.info("API shutdown complete")


app = FastAPI(
    title="LogLens API",
    description=DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
    if elapsed_ms > 1500:
        log.warning("slow request %s %s took %.0f ms", request.method, request.url.path, elapsed_ms)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "status": exc.status_code,
                "detail": exc.detail,
                "path": request.url.path,
                "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "status": 500,
                "detail": "internal server error",
                "type": type(exc).__name__,
                "path": request.url.path,
                "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        },
    )


for router in (overview.router, explore.router, security.router, performance.router, geo.router, system.router):
    app.include_router(router, prefix="/api")
app.include_router(ws.router)


@app.get("/", include_in_schema=False)
async def root() -> Dict[str, Any]:
    return {
        "service": "loglens-api",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/api/health",
        "websockets": ["/ws/live", "/ws/logs"],
    }


def run() -> None:  # pragma: no cover - entry point
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api.host,
        port=settings.api.port,
        log_level=settings.api.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
