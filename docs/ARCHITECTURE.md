# Architecture

This document explains *why* the system is built the way it is. For how to run it, see the
[README](../README.md).

---

## 1. The problem shape

Web access logs are a high-volume, unbounded, late-arriving, heavily-seasonal stream. Any honest
design has to answer four questions:

1. **How do events get in** without the producer and consumer drifting apart, and without back-pressure
   silently turning into data loss?
2. **How is the stream aggregated** into something queryable, with bounded state and correct handling
   of late events?
3. **What counts as an anomaly** in data whose "normal" changes by the hour, day of week and season?
4. **How does an operator act on it** without drowning in one alert per attacking IP per minute?

Each layer below exists to answer one of those.

---

## 2. Ingestion

### Schema as a single artefact

`services/common/loglens_common/schemas.py` defines the log event **once**. The generator constructs
it, Spark parses it via `spark_log_schema()` (the same field list, mechanically converted to a
`StructType`), the API types responses from it, and the ML pipeline reads features from it.

This is deliberate. The classic failure mode of a streaming project is a producer that adds a field,
a consumer that silently drops it, and a dashboard that shows a plausible-looking wrong number for a
month.

### Kafka configuration

| Choice | Value | Reason |
|---|---|---|
| Mode | KRaft (no ZooKeeper) | one fewer moving part; the current supported topology |
| Partitions | 6 | parallelism for Spark; keyed by source IP so per-IP ordering holds |
| Key | source IP | all requests from one client land on one partition |
| Compression | lz4 | ~4× reduction at negligible CPU on JSON |
| `linger.ms` | 25 | batches without adding meaningful latency at these rates |
| Retention | 6 h | enough to replay after a crash; small enough for a laptop |

The producer handles a full local queue by calling `poll()` and retrying rather than dropping —
back-pressure should be *visible*, not silent. `BufferError` handling in
`services/generator/generator/main.py` is where that happens.

### Topics

```
weblogs.raw        ← generator                      (JSON access logs)
weblogs.enriched   ← Spark Q2                       (parsed + derived fields; powers the live tail)
weblogs.anomalies  ← detection sinks                (for downstream consumers)
weblogs.alerts     ← reserved for external routing
```

---

## 3. Stream processing

### One source, eight queries

All eight queries read the same Kafka source and share one enriched DataFrame, but each is an
independent `StreamingQuery` with its own checkpoint directory.

| Query | Grouping | Output mode | Trigger | Sink |
|---|---|---|---|---|
| `raw_logs` | none | append | 5 s | MongoDB (executor-side) |
| `enriched` | none | append | 5 s | Kafka |
| `metrics_global` | `window(1 min)` | update | 10 s | Mongo + global detection |
| `metrics_endpoint` | `window(1 min)`, endpoint, method, service | update | 10 s | Mongo + endpoint detection |
| `metrics_ip` | `window(1 min)`, ip, endpoint | update | 10 s | Mongo + IP detection |
| `metrics_geo` | `window(5 min)`, country | update | 30 s | Mongo + geo detection |
| `metrics_service` | `window(1 min)`, service | update | 10 s | Mongo |
| `sessions` | `session_window(15 min)`, session_id | append | 30 s | Mongo + session detection |

Separate checkpoints mean one query can be restarted (or fail) without replaying the others.
Different triggers mean the raw sink stays responsive for the log tail while the expensive
aggregations run less often.

### Two write patterns, chosen per volume

**Raw events** are written from the executors:

```python
batch_df.foreachPartition(make_raw_writer(uri, database))   # one client per executor process
```

The data never reaches the driver. Buffered at 2 000 documents, `ordered=False`, and duplicate-key
errors are swallowed because Kafka gives at-least-once delivery and `event_id` is unique. Any other
write failure is raised, not logged — a write path that swallows its own errors is indistinguishable
from an idle pipeline.

The connection settings travel *inside the closure* rather than being read from the environment;
see the note on executor configuration in §9.

**Aggregates** are collected with `toPandas()` — a handful of rows per window — because that is where
the detection ensemble runs, and NumPy/pandas is the right tool for it.

### Bounded state

Nothing in the state store grows with traffic volume:

- per-status counts are **conditional sums over a fixed code list**, not `collect_list` (which would
  buffer every event in the window);
- percentiles use `percentile_approx` digests;
- distinct counts use HyperLogLog via `approx_count_distinct`;
- the state store backend is RocksDB, keeping eight queries' state off the JVM heap.

### Grouping on the normalised route

`metrics_ip` groups by `(window, ip, endpoint)` rather than `(window, ip, path)`. With dynamic path
segments (`/products/48213`) almost every request would be a "unique path", which both explodes
cardinality and destroys the scanning signal. Scanner probes (`/.env`, `/wp-admin/`) are their own
endpoints, so enumeration still shows up as high `unique_paths`.

### Late data and window closure

A 2-minute watermark accepts late events. Critically, **detection only fires once a window is
closed** (`window_end` older than the watermark). Structured Streaming re-emits an open window on
every trigger; scoring a partially-filled window would report a traffic collapse every ten seconds.

Because the same closed window may still be emitted more than once, anomaly IDs are content-derived:

```python
anomaly_id = "anm_" + sha1(f"{type}|{entity_type}|{entity}|{window_start}")[:20]
```

Combined with a unique index and upsert-on-write, re-processing updates rather than duplicates.

### Cross-query field contribution

The DDoS detector needs the distribution shape of a window: Gini over per-IP request counts and
entropy over the route mix. Those are only computable where the per-IP rows are — in the IP sink.
So the IP sink computes them and patches them into the global window document, and the global sink
re-reads the merged document before scoring. Because detection is deferred until the window closes,
the ordering resolves itself without coordination.

---

## 4. Detection

### The ensemble

Nine detectors, each returning a normalised `[0,1]` score plus structured evidence:

| Detector | Statistic | Failure mode it covers |
|---|---|---|
| Robust z-score | `(x − median) / (1.4826 · MAD)` | spikes and drops; immune to a single contaminating window |
| EWMA control chart | EW level + EW mean-absolute deviation | sustained shifts, faster than a rolling median |
| Seasonal baseline | same 15-min bucket on prior days, weekday/weekend split | stops the morning ramp from being an incident every day |
| **Trend residual** | Theil-Sen local slope, z-score on the residual | a rising series where no seasonal history exists yet |
| CUSUM | two-sided cumulative sum vs decision limit | small shifts that persist (1 % → 6 % error rate and stays) |
| IsolationForest | path length in a random forest of splits | joint oddities with no single bad metric |
| **Mahalanobis** | covariance-aware distance from the population centre | multivariate cold start, before a model exists |
| Distribution shape | Shannon entropy, Gini coefficient | concentration (DDoS), breadth (scanning), narrowness (scraping) |
| Rule engine | explicit operational budgets | error budgets, per-IP rates, auth failure counts |

Mahalanobis and IsolationForest are deliberately *separate identities* even though both answer
"is this profile unusual". They are correlated views of the same question, and when they shared a
name, fusion counted them as two independent detectors agreeing — which inflated borderline scores
into the critical band.

### Fusion

Scores combine with a **weighted noisy-OR**, not an average:

```
combined = 1 − Π(1 − wᵢ·sᵢ)
score    = combined × 100
```

Two properties matter here:

- **Agreement reinforces.** Three detectors at 0.7 should out-score one at 0.8.
- **Silence does not dilute.** An averaging scheme lets one quiet detector halve a confident one.

Detectors without enough history return *neutral* and are **excluded from fusion entirely** — not
scored as zero. Confidence is reported separately and rises with how many detectors had enough data
to speak.

### Statistical significance is not operational significance

The single largest source of false positives, measured, was detections that were statistically
impeccable and completely irrelevant. Three preconditions now apply *before* scoring:

**Sample size.** An error rate over 22 requests supports no claim — one extra failure moves it five
points. Rate-based detections require a denominator of at least 100 requests; latency percentiles
need ~40, because a p95 is estimable from far fewer samples than a ratio.

**Effect size.** An endpoint whose p95 normally sits at 60 ms and reaches 195 ms is a 12σ event that
nobody should be paged for. Latency detection requires either a slow absolute response (≥ 400 ms and
≥ 1.6× baseline) or a severe relative degradation (≥ 250 ms and ≥ 3× baseline) — the second tier
exists so a genuinely broken fast endpoint is not hidden by a flat floor.

**Population volume.** In a heavy-tailed population, being in the tail is normal: every window
contains clients with one request and a 100% error ratio. The model-only catch-all therefore requires
volume above `max(30, 3 × median)` before it will flag anything on model evidence alone. Rules with
explicit operational meaning — a flood, a scan, credential stuffing — are *not* gated this way,
because they carry their own justification.

### Levels, shares, and what a rise actually means

Two detectors were rewritten during tuning because they were measuring the wrong quantity:

**Endpoint and geographic anomalies use *share*, not absolute volume.** When platform traffic doubles
through the morning, every endpoint and every country doubles with it. Judging each on its share of
the window makes the signal invariant to the overall level, so only a *disproportionate* change
registers — which is also what "unusual origin" actually means: not "India sent a lot of requests"
but "India is 40% of our traffic today instead of 9%".

**Error detection keys on 5xx, not on total errors.** A 401 from an auth-protected route is the
service working correctly; anonymous clients give `/api/v1/cart` a permanent double-digit 4xx rate.
Weighting 4xx equally with 5xx made those endpoints alert continuously. 4xx spikes still surface,
but through the detectors that are actually about client behaviour — endpoint scanning and auth abuse.

### Why not just a threshold

The comparison the project is built to fail is `if requests > X: alert`. Concretely:

- Traffic at 10:00 is 4× traffic at 03:00 — a static threshold either misses night-time attacks or
  fires every morning. *(seasonal detector; trend-residual detector before enough days exist)*
- A rolling median *lags* a rising series, so during the morning ramp every window sits above its own
  baseline and a naive z-score fires continuously. Comparing against a Theil-Sen trend line removes
  that: a smooth ramp has small residuals however steep it is. *(trend-residual detector)*
- A DDoS and a successful marketing campaign both produce a volume spike. What separates them is
  **source concentration and route narrowness**, not volume. *(Gini + entropy + volume, combined
  multiplicatively so all three must agree)*
- An error rate that steps from 1 % to 6 % and stays there stops being an outlier after two windows,
  because it *becomes* the median. *(CUSUM)*
- A client with normal volume, normal error rate, but 180 distinct routes and 88 % 404s is not
  detectable by any single metric. *(multivariate + scan rule)*

### Explanation as a first-class output

Every anomaly carries: the fused score, the severity, each contributing detector with its own score
and evidence dictionary, and a natural-language `reason` assembled from the strongest contributors:

> Request volume spike: 45,000 requests (750.0 rps) in this window. request volume is 45,000.00
> against a rolling median of 7,200.00 (+525%, 12.4σ over 90 windows); request volume is 45,000.00;
> the weekday baseline for 10:00 UTC is 7,240.00 (+11.8σ, n=6)

An anomaly you cannot explain is an anomaly nobody will act on.

### Model discipline

- Models train on the **clean partition only** (windows with no ground-truth label). Fitting an
  outlier model on data containing the attacks teaches it that attacks are normal.
- `RobustScaler`, and `max_features=0.8` so request volume does not dominate the forest.
- Cold start falls back to a **Mahalanobis distance** against the recent population, so multivariate
  detection works before any model exists.
- The streaming path and the offline path import the **same feature list** from `analyzers.py`, and
  derived features are defined so both compute them identically (see `burstiness_from_span`).

---

## 5. Correlation and alerting

One DDoS produces, per minute: one global burst anomaly, one error-rate anomaly, and one anomaly per
attacking IP. Over five minutes with twenty attackers that is ~110 detections describing **one event**.

The correlator groups by `(failure family, entity type, entity)` within a join window, with one
deliberate collapse: per-IP security detections map to `entity = "*"`, so a botnet becomes a single
campaign-level incident. Incidents accumulate a timeline (deduplicated to one entry per minute), an
impact estimate, and a peak-weighted score; they auto-resolve after 15 minutes of silence.

Alert rules are **documents**, not code — editable from the Settings page — matching on type and
minimum severity, with per-`(rule, entity)` cooldowns and content-derived alert IDs so the same
incident cannot page twice.

---

## 6. Serving

FastAPI over Motor. Design rules:

- **Rates are re-derived, never averaged.** `error_rate` over a bucket is `Σerrors / Σrequests`, not
  the mean of per-window rates — averaging a ratio across unequal denominators is simply wrong.
  Latency percentiles *are* averaged across windows, which is an approximation, and the UI says so.
- **Bucketing is automatic.** `auto_bucket_seconds` keeps any series under ~500 points regardless of
  range, so a 7-day query costs the same as a 15-minute one.
- **Counts are bounded.** `count_documents` carries `maxTimeMS` and a limit so a pathological range
  cannot pin the database.
- **Two WebSockets.** `/ws/live` polls MongoDB on a timer (resilient, bounded per connection);
  `/ws/logs` gives each client its own Kafka consumer group with a server-side rate cap, because a
  5 000 rps stream would otherwise drown the browser.

### MongoDB schema

Nine serving collections, all indexed in one declaration (`INDEX_SPEC`) applied idempotently at every
service start. Metric collections are keyed on `(window_start, dimension)` with a unique index, which
is what makes the streaming upserts idempotent. Retention is TTL indexes plus a worker sweep for
collections that need conditional cleanup.

---

## 7. The dashboard

React + TypeScript + Tailwind, Recharts for charts, Three.js for the origin globe, framer-motion and
GSAP-class easing for transitions, Lenis for scroll, TanStack Query for the data layer.

Principles that shaped it:

- **One global time range.** A dashboard where each panel has its own range cannot be reasoned about.
- **A validated palette.** The categorical series colours were checked programmatically for
  colour-vision-deficiency separation (ΔE ≥ 8 on adjacent pairs), a normal-vision floor (ΔE ≥ 15) and
  3:1 contrast against their own surface, in **both** themes. Dark mode is a designed palette, not an
  inverted light one.
- **Status hues are reserved.** good / warning / serious / critical never double as series colours,
  and always ship with a label or icon, so severity survives greyscale and projectors.
- **One y-axis per chart.** Two measures of different scale become two charts.
- **Detections are drawn onto the metric.** Anomaly windows are shaded behind the traffic line, so the
  incident and its cause are in the same glance.
- **The evidence is reachable.** Every detection expands to the detectors that fired, what each
  measured, and the baseline it was measured against.
- **Motion is purposeful.** Page transitions, layout-animated segment pills, count-up on stat tiles,
  a fade for newly-arriving log lines — and everything respects `prefers-reduced-motion`.

---

## 8. Testing and evaluation

Unit and integration tests run **without Kafka, Spark or MongoDB**, because the parts that must be
correct are pure functions:

- detector behaviour (outlier immunity, direction, cold start, CUSUM vs z-score on a sustained shift,
  fusion properties, deterministic IDs);
- metric-document invariants (`Σ status_counts == requests`, `p50 ≤ p95 ≤ p99`, ratios in `[0,1]`);
- analyzer behaviour on synthetic incidents, including the **negative** cases — steady traffic must
  produce nothing, high volume without concentration must not be labelled DDoS, and forty benign
  clients beside one scanner must not be flagged;
- generator realism (diurnal shape, coherent sessions, scenario envelopes and labelling).

Beyond tests, `mlpipeline.evaluate` measures detection quality against injected ground truth and
publishes it to the dashboard. A detector that is not measured is a detector nobody should trust.

Two measurement decisions matter for the numbers to mean anything:

**Recall is reported per-minute *and* per-incident.** A slow-onset incident has minutes at each end
that are labelled but carry no signal — the metric is still at baseline because the injection is
ramping. Per-minute recall punishes those; per-incident recall answers what an operator asks: was it
caught, and how fast. Detection latency is likewise measured once per incident (from the start of
each contiguous run of labelled minutes), not once per labelled minute — the latter reports the
length of the incident rather than the delay in noticing it.

**Incident runs are grouped per label.** Scenarios overlap in time; grouping contiguous labelled
minutes regardless of label attributed every minute to whichever scenario started first, so a
scenario with 100% per-minute recall could report zero incidents.

Both of those were bugs in the harness itself, found by disbelieving its output.

`--detect-only` re-runs detection over metrics already in MongoDB, so tuning a threshold and seeing
its effect on historical incidents takes minutes instead of a full re-simulation. Operationally it
answers "would the new configuration have caught last Tuesday?".

---

## 9. Deliberate limitations

- **Traffic is synthetic.** That is what makes ground-truth measurement possible. Any JSON matching
  the log schema can be produced to `weblogs.raw` instead, and nothing downstream changes.
- **Latency percentiles are averaged across windows** in range queries. Exact global percentiles would
  require persisting the digests; the approximation is noted where it appears.
- **Single-node everything.** One Kafka broker, one Mongo node, one Spark worker by default. The
  topology is horizontally scalable (partitions, `--scale spark-worker=N`, replica sets) but is not
  configured for HA here.
- **Executors carry their own configuration.** Spark executors are launched by the *worker* and
  inherit its environment, not the driver's. Task closures therefore pass connection settings
  explicitly rather than reading them from the process environment — relying on environment parity
  between driver and executors silently routed every raw-log write to `localhost` inside the executor
  container until it was caught.
- **No authentication.** This is a local analytics platform, not an internet-facing service. Adding
  auth means a reverse proxy and an identity provider, not a change to the pipeline.
