"""Uniform logging across every service.

Human-readable by default; set ``LOG_FORMAT=json`` for structured output that a
real log pipeline (including this one) could ingest.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[38;5;245m",
        "INFO": "\033[38;5;39m",
        "WARNING": "\033[38;5;214m",
        "ERROR": "\033[38;5;203m",
        "CRITICAL": "\033[48;5;203;38;5;231m",
    }
    RESET = "\033[0m"

    def __init__(self, use_color: bool = True):
        super().__init__("%(asctime)s %(levelname)-7s %(name)-24s %(message)s", "%H:%M:%S")
        self.use_color = use_color and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if self.use_color:
            color = self.COLORS.get(record.levelname, "")
            return f"{color}{text}{self.RESET}"
        return text


def setup_logging(service: str, level: str | None = None) -> logging.Logger:
    level_name = (level or os.getenv("LOG_LEVEL") or os.getenv("API_LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter() if os.getenv("LOG_FORMAT", "console").lower() == "json" else ConsoleFormatter()
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level_name, logging.INFO))

    for noisy in ("pymongo", "kafka", "urllib3", "asyncio", "py4j", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger(f"loglens.{service}")
    logger.info("logging initialised (level=%s)", level_name)
    return logger
