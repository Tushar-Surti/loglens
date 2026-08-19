"""Time-range parsing and bucketing shared by the API and the worker."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

_RANGE_RE = re.compile(r"^(\d+)\s*(s|m|h|d|w)$", re.IGNORECASE)

_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}

# Range → (bucket size in seconds) so a chart never returns more than ~500 points.
_BUCKET_LADDER = [
    (15 * 60, 60),
    (60 * 60, 60),
    (6 * 3600, 300),
    (24 * 3600, 900),
    (3 * 86400, 3600),
    (7 * 86400, 3600),
    (30 * 86400, 21600),
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_duration(value: str, default_seconds: int = 3600) -> timedelta:
    """``"15m"``, ``"6h"``, ``"7d"`` → :class:`timedelta`."""
    if not value:
        return timedelta(seconds=default_seconds)
    match = _RANGE_RE.match(value.strip())
    if not match:
        return timedelta(seconds=default_seconds)
    amount, unit = int(match.group(1)), match.group(2).lower()
    return timedelta(**{_UNITS[unit]: amount})


def resolve_range(
    range_: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    default: str = "1h",
) -> Tuple[datetime, datetime]:
    """Explicit ``start``/``end`` win; otherwise a relative range ending now."""
    if start and end:
        return _as_utc(start), _as_utc(end)
    now = utcnow()
    if end and not start:
        end_utc = _as_utc(end)
        return end_utc - parse_duration(range_ or default), end_utc
    if start and not end:
        return _as_utc(start), now
    return now - parse_duration(range_ or default), now


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def auto_bucket_seconds(start: datetime, end: datetime, max_points: int = 500) -> int:
    """Pick a bucket size that keeps a series readable and cheap to transfer."""
    span = max((end - start).total_seconds(), 60)
    for limit, bucket in _BUCKET_LADDER:
        if span <= limit:
            return bucket
    return max(int(span / max_points / 60) * 60, 60)


def floor_to(dt: datetime, seconds: int) -> datetime:
    epoch = int(_as_utc(dt).timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return _as_utc(dt).isoformat().replace("+00:00", "Z") if dt else None
