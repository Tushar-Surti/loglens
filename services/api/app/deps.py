"""Shared FastAPI dependencies: database handle, time range, pagination."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Query

from loglens_common.mongo import get_async_db
from loglens_common.timeutil import auto_bucket_seconds, resolve_range

_db = None


def db():
    """Singleton Motor database bound to the running event loop."""
    global _db
    if _db is None:
        _db = get_async_db()
    return _db


class TimeRange:
    def __init__(self, start: datetime, end: datetime, label: str):
        self.start = start
        self.end = end
        self.label = label
        self.bucket_seconds = auto_bucket_seconds(start, end)

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0

    def filter(self, field: str = "window_start") -> Dict[str, Any]:
        return {field: {"$gte": self.start, "$lte": self.end}}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start.isoformat().replace("+00:00", "Z"),
            "end": self.end.isoformat().replace("+00:00", "Z"),
            "label": self.label,
            "bucket_seconds": self.bucket_seconds,
        }


def time_range(
    range: str = Query("1h", description="Relative range such as 15m, 1h, 24h, 7d"),
    start: Optional[datetime] = Query(None, description="Explicit ISO start (overrides range)"),
    end: Optional[datetime] = Query(None, description="Explicit ISO end"),
) -> TimeRange:
    resolved_start, resolved_end = resolve_range(range, start, end)
    if resolved_end <= resolved_start:
        raise HTTPException(status_code=400, detail="end must be after start")
    if (resolved_end - resolved_start).days > 92:
        raise HTTPException(status_code=400, detail="range must not exceed 92 days")
    return TimeRange(resolved_start, resolved_end, range if not (start and end) else "custom")


class Pagination:
    def __init__(self, limit: int, offset: int):
        self.limit = limit
        self.offset = offset


def pagination(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0, le=100_000),
) -> Pagination:
    return Pagination(limit, offset)


TimeRangeDep = Depends(time_range)
PaginationDep = Depends(pagination)
