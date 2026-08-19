"""Offline training pipeline.

Produces three artefacts, all consumed by the live system:

``global_isoforest.joblib``
    IsolationForest over platform-level window features — catches traffic
    profiles that are jointly unusual without any single metric breaching a
    threshold.
``ip_isoforest.joblib``
    IsolationForest over per-source-IP behavioural features — the population
    model behind "this client does not behave like the others".
``traffic_profiles``
    KMeans clusters over the daily traffic shape, stored in Mongo and rendered
    on the Traffic Patterns view.

Also recomputes the **seasonal baseline** (median and IQR per time-of-day
bucket, weekday vs weekend), which the dashboard draws as the expected band
behind live charts.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler, StandardScaler

from loglens_common.analyzers import GLOBAL_FEATURES, IP_FEATURES
from loglens_common.config import settings
from loglens_common.logging_setup import setup_logging
from loglens_common.mongo import get_db
from loglens_common.schemas import Collections

from .features import global_feature_frame, ip_feature_frame, split_clean

log = setup_logging("ml.train")


def _load(collection: str, hours: float, db) -> pd.DataFrame:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    docs = list(db[collection].find({"window_start": {"$gte": since}}, {"_id": 0}).sort("window_start", 1))
    if not docs:
        return pd.DataFrame()
    frame = pd.DataFrame(docs)
    frame["window_start"] = pd.to_datetime(frame["window_start"], utc=True)
    return frame


def _fit_isolation_forest(
    matrix: pd.DataFrame,
    features: List[str],
    contamination: float,
    trees: int,
    seed: int,
    scaler_kind: str = "robust",
) -> Dict[str, Any]:
    scaler = RobustScaler() if scaler_kind == "robust" else StandardScaler()
    scaled = scaler.fit_transform(matrix.values)
    model = IsolationForest(
        n_estimators=trees,
        contamination=contamination,
        max_samples="auto",
        # Sub-sampling features makes the forest less dominated by request
        # volume, which otherwise swamps the behavioural signals.
        max_features=0.8,
        bootstrap=False,
        random_state=seed,
        n_jobs=-1,
    )
    model.fit(scaled)
    scores = model.decision_function(scaled)
    return {
        "model": model,
        "scaler": scaler,
        "features": features,
        "metadata": {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "rows": int(matrix.shape[0]),
            "trees": trees,
            "contamination": contamination,
            "scaler": scaler_kind,
            "score_mean": float(np.mean(scores)),
            "score_std": float(np.std(scores)),
            "score_p01": float(np.percentile(scores, 1)),
            "score_p05": float(np.percentile(scores, 5)),
            "feature_medians": {f: float(matrix[f].median()) for f in features},
        },
    }


def _save(bundle: Dict[str, Any], path: str, name: str, db) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    joblib.dump(bundle, path)
    db[Collections.MODELS].insert_one(
        {
            "name": name,
            "path": path,
            "trained_at": datetime.now(timezone.utc),
            "features": bundle["features"],
            "metadata": bundle["metadata"],
        }
    )
    log.info("saved %s → %s (%s rows)", name, path, bundle["metadata"]["rows"])


def train_global(hours: float, args, db) -> Optional[Dict[str, Any]]:
    metrics = _load(Collections.METRICS_GLOBAL, hours, db)
    if metrics.empty:
        log.warning("no global metrics available — skipping global model")
        return None

    clean, labelled = split_clean(metrics)
    if len(clean) < settings.ml.min_training_rows:
        log.warning(
            "only %d clean global windows (need %d) — training on everything instead",
            len(clean), settings.ml.min_training_rows,
        )
        clean = metrics
    matrix = global_feature_frame(clean)
    bundle = _fit_isolation_forest(
        matrix, GLOBAL_FEATURES, args.contamination, args.trees, args.seed, "robust"
    )
    bundle["metadata"].update(
        {"clean_rows": int(len(clean)), "labelled_rows": int(len(labelled)), "window": "1min", "hours": hours}
    )
    _save(bundle, os.path.join(args.model_dir, "global_isoforest.joblib"), "global_isoforest", db)
    return bundle


def train_ip(hours: float, args, db) -> Optional[Dict[str, Any]]:
    metrics = _load(Collections.METRICS_IP, min(hours, 12), db)
    if metrics.empty:
        log.warning("no IP metrics available — skipping IP model")
        return None

    clean, labelled = split_clean(metrics)
    if len(clean) < settings.ml.min_training_rows:
        clean = metrics
    # Cap the population so a single noisy hour cannot dominate the fit.
    if len(clean) > args.max_rows:
        clean = clean.sample(args.max_rows, random_state=args.seed)

    matrix = ip_feature_frame(clean)
    bundle = _fit_isolation_forest(
        matrix, IP_FEATURES, max(args.contamination * 2, 0.02), args.trees, args.seed, "robust"
    )
    bundle["metadata"].update(
        {"clean_rows": int(len(clean)), "labelled_rows": int(len(labelled)), "window": "1min", "hours": hours}
    )
    _save(bundle, os.path.join(args.model_dir, "ip_isoforest.joblib"), "ip_isoforest", db)
    return bundle


def train_traffic_profiles(hours: float, args, db) -> Optional[Dict[str, Any]]:
    """Cluster hourly traffic shapes into interpretable operating profiles."""
    metrics = _load(Collections.METRICS_GLOBAL, hours, db)
    if metrics.empty or len(metrics) < 120:
        log.warning("not enough history for traffic profiling")
        return None

    frame = metrics.copy()
    frame["hour"] = frame["window_start"].dt.floor("h")
    hourly = frame.groupby("hour").agg(
        requests=("requests", "sum"),
        error_rate=("error_rate", "mean"),
        p95=("p95_response_time", "mean"),
        unique_ips=("unique_ips", "mean"),
        bot_ratio=("bot_ratio", "mean"),
    ).reset_index()
    if len(hourly) < 8:
        return None

    features = ["requests", "error_rate", "p95", "unique_ips", "bot_ratio"]
    scaler = StandardScaler()
    scaled = scaler.fit_transform(hourly[features].fillna(0.0).values)
    k = int(min(args.clusters, max(2, len(hourly) // 3)))
    kmeans = KMeans(n_clusters=k, n_init=10, random_state=args.seed)
    hourly["cluster"] = kmeans.fit_predict(scaled)

    centers = scaler.inverse_transform(kmeans.cluster_centers_)
    profiles: List[Dict[str, Any]] = []
    ranked = np.argsort(centers[:, 0])
    names = ["quiet", "off-peak", "steady", "busy", "peak", "surge"]
    for rank, cluster in enumerate(ranked):
        members = hourly[hourly["cluster"] == cluster]
        profiles.append(
            {
                "cluster": int(cluster),
                "name": names[min(rank, len(names) - 1)],
                "hours": int(len(members)),
                "avg_requests_per_hour": round(float(centers[cluster][0]), 1),
                "avg_error_rate": round(float(centers[cluster][1]), 5),
                "avg_p95_ms": round(float(centers[cluster][2]), 1),
                "avg_unique_ips": round(float(centers[cluster][3]), 1),
                "avg_bot_ratio": round(float(centers[cluster][4]), 4),
                "typical_hours_utc": sorted({int(h.hour) for h in members["hour"]}),
            }
        )

    db[Collections.BASELINES].update_one(
        {"key": "traffic_profiles"},
        {"$set": {"value": profiles, "k": k, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    log.info("traffic profiles: %s", ", ".join(f"{p['name']}({p['hours']}h)" for p in profiles))
    return {"profiles": profiles}


def compute_seasonal_baseline(hours: float, args, db) -> Optional[Dict[str, Any]]:
    """Median / IQR per 15-minute bucket, split by weekday vs weekend."""
    metrics = _load(Collections.METRICS_GLOBAL, hours, db)
    if metrics.empty:
        return None

    frame = metrics.copy()
    stamps = frame["window_start"]
    frame["bucket"] = (stamps.dt.hour * 60 + stamps.dt.minute) // 15
    frame["day_type"] = np.where(stamps.dt.dayofweek >= 5, "weekend", "weekday")

    tracked = ["requests", "error_rate", "p95_response_time", "unique_ips"]
    baseline: Dict[str, Any] = {}
    for (day_type, bucket), group in frame.groupby(["day_type", "bucket"]):
        entry = baseline.setdefault(day_type, {})
        entry[str(int(bucket))] = {
            metric: {
                "median": round(float(group[metric].median()), 4),
                "p25": round(float(group[metric].quantile(0.25)), 4),
                "p75": round(float(group[metric].quantile(0.75)), 4),
                "p95": round(float(group[metric].quantile(0.95)), 4),
                "samples": int(group[metric].count()),
            }
            for metric in tracked
            if metric in group
        }

    db[Collections.BASELINES].update_one(
        {"key": "seasonal"},
        {
            "$set": {
                "value": baseline,
                "bucket_minutes": 15,
                "source_windows": int(len(frame)),
                "updated_at": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    log.info("seasonal baseline rebuilt from %d windows", len(frame))
    return baseline


def run(args) -> Dict[str, Any]:
    db = get_db()
    os.makedirs(args.model_dir, exist_ok=True)
    results = {
        "global": bool(train_global(args.hours, args, db)),
        "ip": bool(train_ip(args.hours, args, db)),
        "profiles": bool(train_traffic_profiles(args.hours, args, db)),
        "seasonal": bool(compute_seasonal_baseline(args.hours, args, db)),
    }
    db[Collections.CONFIG].update_one(
        {"key": "last_training"},
        {"$set": {"value": {**results, "at": datetime.now(timezone.utc), "hours": args.hours}}},
        upsert=True,
    )
    log.info("training complete: %s", json.dumps(results))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loglens-train", description="Train the LogLens anomaly models.")
    parser.add_argument("--hours", type=float, default=72.0, help="history window to train on")
    parser.add_argument("--trees", type=int, default=200)
    parser.add_argument("--contamination", type=float, default=settings.detection.isoforest_contamination)
    parser.add_argument("--clusters", type=int, default=5)
    parser.add_argument("--max-rows", type=int, default=120_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model-dir", default=settings.ml.model_dir)
    return parser


def main() -> int:
    run(build_parser().parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
