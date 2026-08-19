"""Feature extraction for the unsupervised models.

The feature *definitions* live in :mod:`loglens_common.analyzers` so that
training and scoring can never drift apart — the trainer imports the same list
the streaming sink uses.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd

from loglens_common.analyzers import GLOBAL_FEATURES, IP_FEATURES


def _ensure_columns(frame: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = 0.0
    return out


def global_feature_frame(metrics: pd.DataFrame) -> pd.DataFrame:
    """Derive the global feature matrix from ``metrics_global`` documents."""
    frame = _ensure_columns(metrics, ["requests", "bytes_sent"] + GLOBAL_FEATURES)
    if "rps" not in metrics.columns or frame["rps"].fillna(0).eq(0).all():
        frame["rps"] = frame["requests"] / 60.0
    if "bytes_per_request" not in metrics.columns:
        frame["bytes_per_request"] = frame["bytes_sent"] / frame["requests"].clip(lower=1)

    # Time-of-day encoded on the unit circle so 23:59 and 00:01 stay adjacent.
    if "window_start" in frame.columns:
        stamps = pd.to_datetime(frame["window_start"], utc=True)
        minute = stamps.dt.hour * 60 + stamps.dt.minute
        frame["tod_sin"] = np.sin(2 * np.pi * minute / 1440)
        frame["tod_cos"] = np.cos(2 * np.pi * minute / 1440)
        frame["is_weekend"] = (stamps.dt.dayofweek >= 5).astype(float)

    return frame[GLOBAL_FEATURES].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def ip_feature_frame(metrics: pd.DataFrame) -> pd.DataFrame:
    """Derive the per-IP feature matrix from ``metrics_ip`` documents."""
    frame = _ensure_columns(metrics, IP_FEATURES + ["requests", "bytes_sent"])
    if "bytes_per_request" not in metrics.columns:
        frame["bytes_per_request"] = frame["bytes_sent"] / frame["requests"].clip(lower=1)
    return frame[IP_FEATURES].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def split_clean(metrics: pd.DataFrame, label_column: str = "dominant_attack_label") -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split into (clean, labelled) — models train on the clean partition.

    Training an outlier model on data that already contains the attacks teaches
    it that attacks are normal.  A real deployment picks a known-good period;
    here the injected ground-truth labels let us do exactly that.
    """
    if label_column not in metrics.columns:
        return metrics, metrics.iloc[0:0]
    labelled = metrics[metrics[label_column].notna()]
    clean = metrics[metrics[label_column].isna()]
    return clean, labelled
