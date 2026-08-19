"""MongoDB → JSON serialisation helpers."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

try:  # pragma: no cover - bson is always present with pymongo
    from bson import ObjectId
except ImportError:  # pragma: no cover
    ObjectId = None  # type: ignore


def jsonable(value: Any) -> Any:
    """Recursively convert BSON/py types into JSON-safe values.

    Timestamps always leave as ISO-8601 with a ``Z`` suffix so the frontend can
    parse them with ``new Date()`` without timezone guesswork.
    """
    if value is None:
        return None
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        stamp = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return stamp.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if ObjectId is not None and isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return str(value)


def doc(document: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return jsonable(document) if document else None


def docs(documents: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [jsonable(item) for item in documents]


def page(items: List[Dict[str, Any]], total: Optional[int] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    return {
        "items": docs(items),
        "count": len(items),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (total is not None and offset + len(items) < total) or (total is None and len(items) == limit),
    }
