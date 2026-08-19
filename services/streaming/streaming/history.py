"""Incremental history cache used by the detection sinks.

Detectors need a rolling baseline (up to several days for the seasonal model).
Re-reading that from MongoDB on every micro-batch would dominate the batch
time, so each sink keeps an in-memory frame and only fetches documents newer
than what it already holds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

log = logging.getLogger("loglens.streaming.history")


class MetricHistory:
    def __init__(
        self,
        collection: str,
        lookback_minutes: int = 4320,
        time_field: str = "window_start",
        projection: Optional[List[str]] = None,
        max_rows: int = 60_000,
        refresh_seconds: float = 20.0,
    ):
        self.collection = collection
        self.lookback = timedelta(minutes=lookback_minutes)
        self.time_field = time_field
        self.projection = projection
        self.max_rows = max_rows
        self.refresh_seconds = refresh_seconds
        self._frame = pd.DataFrame()
        self._high_water: Optional[datetime] = None
        self._last_refresh: Optional[datetime] = None

    def _query(self, db, since: datetime, after: Optional[datetime]) -> List[Dict[str, Any]]:
        criteria: Dict[str, Any] = {self.time_field: {"$gte": since}}
        if after is not None:
            criteria[self.time_field] = {"$gt": after}
        projection = {field: 1 for field in self.projection} if self.projection else None
        if projection:
            projection["_id"] = 0
        cursor = db[self.collection].find(criteria, projection).sort(self.time_field, 1).limit(self.max_rows)
        return list(cursor)

    def refresh(self, db, force: bool = False) -> pd.DataFrame:
        now = datetime.now(timezone.utc)
        if (
            not force
            and self._last_refresh is not None
            and (now - self._last_refresh).total_seconds() < self.refresh_seconds
        ):
            return self._frame

        since = now - self.lookback
        try:
            docs = self._query(db, since, self._high_water)
        except Exception as exc:  # pragma: no cover - Mongo hiccup must not kill the batch
            log.warning("history refresh for %s failed: %s", self.collection, exc)
            return self._frame

        self._last_refresh = now
        if docs:
            fresh = pd.DataFrame(docs)
            fresh[self.time_field] = pd.to_datetime(fresh[self.time_field], utc=True)
            self._frame = (
                pd.concat([self._frame, fresh], ignore_index=True) if not self._frame.empty else fresh
            )
            self._high_water = self._frame[self.time_field].max().to_pydatetime()

        if not self._frame.empty:
            cutoff = pd.Timestamp(since)
            self._frame = self._frame[self._frame[self.time_field] >= cutoff]
            if len(self._frame) > self.max_rows:
                self._frame = self._frame.tail(self.max_rows)
            self._frame = self._frame.reset_index(drop=True)
        return self._frame

    def append(self, rows: pd.DataFrame) -> None:
        """Fold the current batch in so consecutive windows see each other."""
        if rows is None or rows.empty:
            return
        rows = rows.copy()
        rows[self.time_field] = pd.to_datetime(rows[self.time_field], utc=True)
        self._frame = pd.concat([self._frame, rows], ignore_index=True) if not self._frame.empty else rows
        self._frame = self._frame.drop_duplicates(
            subset=[c for c in [self.time_field, "endpoint", "method", "ip", "country"] if c in self._frame.columns],
            keep="last",
        ).reset_index(drop=True)
        if self._high_water is None or rows[self.time_field].max().to_pydatetime() > self._high_water:
            self._high_water = rows[self.time_field].max().to_pydatetime()

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    def before(self, moment: datetime) -> pd.DataFrame:
        """History strictly older than ``moment`` — never let a window score itself."""
        if self._frame.empty:
            return self._frame
        return self._frame[self._frame[self.time_field] < pd.Timestamp(moment)]
