"""A small query language for the Log Explorer.

Supported syntax (space separated, all optional, combined with AND)::

    status:500              field equality
    status>=400             numeric comparison  (> >= < <= =)
    endpoint:/api/v1/*      glob → anchored regex
    ip:203.0.113.5
    method:POST
    country:DE
    service:checkout-svc
    rt>800                  alias for response_time_ms
    bytes>100000
    is_bot:true
    -status:200             negation
    checkout                bare term → substring match on path / UA / IP

Anything unparseable degrades to a free-text term rather than erroring: an
explorer that rejects your keystrokes mid-typing is worse than one that
searches broadly.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional, Tuple

FIELD_ALIASES: Dict[str, str] = {
    "rt": "response_time_ms",
    "latency": "response_time_ms",
    "response_time": "response_time_ms",
    "bytes": "bytes_sent",
    "size": "bytes_sent",
    "code": "status",
    "url": "path",
    "route": "endpoint",
    "ua": "user_agent",
    "agent": "user_agent",
    "user": "user_id",
    "session": "session_id",
    "geo": "country",
    "svc": "service",
    "label": "attack_label",
}

NUMERIC_FIELDS = {"status", "response_time_ms", "bytes_sent", "upstream_time_ms", "bytes_received"}
BOOLEAN_FIELDS = {"is_bot", "is_error", "is_server_error"}
ALLOWED_FIELDS = NUMERIC_FIELDS | BOOLEAN_FIELDS | {
    "ip", "method", "path", "endpoint", "country", "country_name", "city", "service", "host",
    "user_agent", "user_id", "session_id", "status_class", "cache_status", "device", "browser",
    "os", "asn", "org", "region", "attack_label", "event_id", "request_id",
}

OPERATORS = {">=": "$gte", "<=": "$lte", ">": "$gt", "<": "$lt", "!=": "$ne", "=": "$eq", ":": "$eq"}
_TOKEN_RE = re.compile(r"^(-)?([a-zA-Z_]+)\s*(>=|<=|!=|>|<|=|:)\s*(.+)$")


def _coerce(field: str, raw: str) -> Any:
    if field in NUMERIC_FIELDS:
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return raw
    if field in BOOLEAN_FIELDS:
        return raw.strip().lower() in {"1", "true", "yes"}
    return raw


def _glob_to_regex(pattern: str) -> Dict[str, str]:
    escaped = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return {"$regex": f"^{escaped}$", "$options": "i"}


def parse(query: Optional[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Return ``(mongo_filter, free_text_terms)``."""
    if not query or not query.strip():
        return {}, []

    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()

    conditions: List[Dict[str, Any]] = []
    free_text: List[str] = []

    for token in tokens:
        match = _TOKEN_RE.match(token)
        if not match:
            free_text.append(token)
            continue

        negated, field, operator, raw = match.groups()
        field = FIELD_ALIASES.get(field.lower(), field.lower())
        if field not in ALLOWED_FIELDS:
            free_text.append(token)
            continue

        value = _coerce(field, raw)
        if operator in (":", "=") and isinstance(value, str) and ("*" in raw or "?" in raw):
            condition = {field: _glob_to_regex(raw)}
        elif operator in (":", "="):
            condition = {field: value}
        else:
            condition = {field: {OPERATORS[operator]: value}}

        conditions.append({"$nor": [condition]} if negated else condition)

    if free_text:
        pattern = "|".join(re.escape(term) for term in free_text)
        conditions.append(
            {
                "$or": [
                    {"path": {"$regex": pattern, "$options": "i"}},
                    {"user_agent": {"$regex": pattern, "$options": "i"}},
                    {"ip": {"$regex": pattern, "$options": "i"}},
                    {"endpoint": {"$regex": pattern, "$options": "i"}},
                    {"session_id": {"$regex": pattern, "$options": "i"}},
                ]
            }
        )

    if not conditions:
        return {}, free_text
    if len(conditions) == 1:
        return conditions[0], free_text
    return {"$and": conditions}, free_text


def describe(query: Optional[str]) -> Dict[str, Any]:
    """Explain a parsed query — powers the "what am I filtering on" chip row."""
    filter_, free_text = parse(query)
    return {"filter": filter_, "free_text": free_text, "fields": sorted(ALLOWED_FIELDS)}
