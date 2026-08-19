# LogLens

**Real-time web log monitoring and anomaly detection.**
A working, production-shaped observability platform: high-volume log ingestion through Kafka,
distributed windowed analytics in Spark Structured Streaming, a statistical + ML detection
ensemble, MongoDB for serving, and an operator dashboard built for people who stare at it all day.

```
 log sources ──▶ Kafka ──▶ Spark Structured Streaming ──▶ detection ──▶ MongoDB ──▶ API ──▶ dashboard
  (generator)   6 partitions   8 stateful queries        ensemble       9 collections   FastAPI    React
                               1-min tumbling windows    fused 0-100                    + WS
                               15-min session windows    scores
                                                         9 detectors
```

---

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Anomaly detection](#anomaly-detection)
- [The dashboard](#the-dashboard)
- [Project layout](#project-layout)
- [Configuration](#configuration)
- [API](#api)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## What it does

**Ingest and process**
- Synthetic traffic generator producing realistic access logs at configurable volume (default ~180 rps,
  tested to several thousand), with diurnal demand, Markov-chain user journeys, log-normal latency,
  geography, devices, crawlers and cache behaviour.
- Kafka as the ingestion buffer (KRaft mode, 6 partitions, lz4 compression, keyed by source IP).
- Spark Structured Streaming: eight concurrent stateful queries over one Kafka source — tumbling
  1-minute windows for platform / endpoint / source-IP / service metrics, 5-minute windows for
  geography, gap-based **session windows** for user journeys, plus a raw sink and a re-published
  enriched topic.

**Detect**
- Nine independent detectors — robust z-score, EWMA control chart, seasonal (time-of-day) baseline,
  Theil-Sen trend residual, CUSUM change-point, IsolationForest, Mahalanobis distance,
  distribution-shape measures (entropy / Gini), and a domain rule engine — fused into a single
  0–100 score with a severity ladder.
- Detects traffic spikes and drops, DDoS-like bursts, error-rate spikes, latency degradation, slow
  endpoints, scanning, credential stuffing, scraping, bot surges, geographic shifts, abnormal sessions
  and multivariate profile anomalies.
- Every detection carries a **human-readable explanation**, the per-detector evidence, and the
  baseline it was measured against.

**Operate**
- Anomalies are correlated into incidents (a 20-host botnet is one incident, not twenty), with a
  timeline, impact estimate and auto-resolution.
- Alert rules are documents, editable from the UI, with per-entity cooldowns and optional webhooks.
- Detection quality is **measured**: the generator labels every injected incident, and an evaluation
  harness reports precision, recall, F1 and detection latency per scenario — shown on the Anomalies page.

**Explore**
- Log Explorer with a query language, faceting, a match histogram, virtualised results and a live tail
  straight off Kafka.
- Endpoint, session, geography and system-health views, all sharing one global time range.

---

## Quick start

**Requirements:** Docker Desktop (or Docker Engine + Compose v2), ~6 GB free RAM, ports
3000 / 3001 / 8000 / 8080 / 9090 / 27017 / 29092 free.

```bash
git clone <this repo> && cd BDA-Project
cp .env.example .env

# One command: build, start, backfill 6h of history, train the models
./scripts/bootstrap.sh          # Windows: .\scripts\bootstrap.ps1
```

or with make:

```bash
make demo
```

Then open:

| Surface | URL | Notes |
|---|---|---|
| **Dashboard** | http://localhost:3000 | the product |
| API docs | http://localhost:8000/docs | OpenAPI, every endpoint |
| Grafana | http://localhost:3001 | `admin` / `loglens` |
| Spark UI | http://localhost:9090 | streaming query progress |
| Spark master | http://localhost:8080 | cluster state |

### The 60-second demo

1. Open the dashboard. Traffic is already flowing; the backfill gave you history and a baseline.
2. Click **Simulate** in the top bar → **DDoS Burst**.
3. Watch, in order:
   - the **Overview** throughput chart bends upward and a shaded band appears where detection fired;
   - **Live detections** streams in over the WebSocket;
   - **Incidents** gains one correlated record — not one per attacking IP;
   - **Anomalies → detection detail** shows exactly which detectors agreed and what they measured;
   - **Geography** lights up the hostile origins on the globe.
4. Nothing about that path is faked: the scenario is queued to the generator, produced to Kafka,
   aggregated by Spark, scored by the detectors and correlated by the worker.

---

## Architecture

### Data flow

```
┌──────────────┐   JSON, keyed by IP    ┌──────────────┐
│  generator   │───────────────────────▶│    Kafka     │  weblogs.raw (6 partitions)
│  (scenarios) │                        │    KRaft     │  weblogs.enriched
└──────┬───────┘                        └──────┬───────┘  weblogs.anomalies
       │ control plane                          │
       │ (scenario queue)                       ▼
       │                          ┌──────────────────────────────┐
       │                          │  Spark Structured Streaming  │
       │                          │  parse → enrich → watermark  │
       │                          ├──────────────────────────────┤
       │                          │ Q1 raw       → executor bulk │
       │                          │ Q2 enriched  → Kafka         │
       │                          │ Q3 1-min global   + detect   │
       │                          │ Q4 1-min endpoint + detect   │
       │                          │ Q5 1-min ip×route + detect   │
       │                          │ Q6 5-min geography+ detect   │
       │                          │ Q7 1-min service             │
       │                          │ Q8 session window + detect   │
       │                          └──────────────┬───────────────┘
       │                                         ▼
       │                          ┌──────────────────────────────┐
       └─────────────────────────▶│           MongoDB            │◀── worker
                                  │  metrics · anomalies ·       │    correlation
                                  │  incidents · alerts · config │    alerting
                                  └──────────────┬───────────────┘    health, retention
                                                 ▼                     retraining
                                  ┌──────────────────────────────┐
                                  │        FastAPI + WS          │
                                  └──────────────┬───────────────┘
                                                 ▼
                                  ┌──────────────────────────────┐
                                  │   React dashboard · Grafana  │
                                  └──────────────────────────────┘
```

### Why it is shaped this way

**One Kafka source, eight queries.** Each stateful query owns its checkpoint directory, so any one can
be restarted without replaying the others, and each gets an independent trigger cadence (5 s for the
raw sink so the log tail stays live; 10 s for aggregates; 30 s for the coarse ones).

**Raw events never pass through the driver.** The raw sink uses `foreachPartition` with a per-worker
Mongo client — writes stay distributed. Aggregates *do* collect to the driver (a handful of rows per
window) because that is where the detection ensemble runs.

**Detection only fires on closed windows.** Structured Streaming re-emits an open window on every
trigger; scoring a partially-filled window would report a traffic collapse every ten seconds. Sinks
gate detection on `window_end` older than the watermark, and anomaly IDs are content-derived
(`sha1(type|entity|window)`) so re-processing updates instead of duplicating.

**Bounded state.** Per-status counts are conditional sums over a fixed code list, not `collect_list`;
percentiles use `percentile_approx` digests; distinct counts use HyperLogLog (`approx_count_distinct`).
Nothing in the state store grows with traffic volume.

**Source concentration is computed where the data is.** Gini over per-IP counts and entropy over the
route mix are calculated in the IP sink and merged into the global window document — that is what lets
the DDoS detector separate "a lot of traffic" from "a lot of traffic from eight hosts hitting one route".

**MongoDB is a single-node replica set**, so change streams and transactions behave as they would in
production. Retention is enforced by TTL indexes plus a worker sweep.

---

## Anomaly detection

The core claim of this project is that detection is **not** a threshold check. Seven detectors run per
window; each returns a normalised `[0,1]` score plus its own evidence, and a weighted noisy-OR fuses
them into `0–100`.

| Detector | Question it answers | Catches |
|---|---|---|
| **Robust z-score** (median/MAD) | Is this window an outlier against its recent history? | spikes, drops |
| **EWMA control chart** | Has the level shifted away from the forecast? | sustained ramps |
| **Seasonal baseline** | Is this unusual *for 10:00 on a weekday*? | avoids flagging every morning ramp |
| **CUSUM change-point** | Has a small shift persisted long enough to matter? | error rate stepping 1 % → 6 % and staying |
| **IsolationForest** | Does this profile look like anything we have seen? | multivariate oddities with no single bad metric |
| **Distribution shape** (entropy, Gini) | Is traffic concentrated or scattered abnormally? | DDoS concentration, scanning breadth, scraping narrowness |
| **Rule engine** | Does this breach an explicit operational budget? | error budgets, per-IP rates, auth failures |

Design decisions worth calling out:

- **Robust statistics throughout.** Median and MAD instead of mean and standard deviation, so one
  extreme window cannot poison the baseline it is compared against.
- **Seasonality is modelled explicitly.** Comparing 10:00 against the previous 30 minutes flags every
  morning ramp. The seasonal detector compares against the same 15-minute bucket on previous days,
  with weekday and weekend profiles kept separate.
- **Cold start is handled.** Detectors without enough history return *neutral* and are excluded from
  fusion (not scored as zero, which would dilute a confident detector). The multivariate detector falls
  back to a Mahalanobis distance until a model exists.
- **Score fusion is noisy-OR, not an average.** Independent detectors agreeing should reinforce each
  other; one silent detector must not dilute a confident one. Confidence rises with how many detectors
  had enough data to speak.
- **Models are trained on clean data.** The trainer splits on ground-truth labels and fits the
  IsolationForest on the unlabelled partition — training an outlier model on data containing the
  attacks teaches it that attacks are normal.
- **Features cannot drift.** The streaming path and the offline/backfill path import the *same*
  feature list, and derived features (like burstiness) are defined so both compute them identically.

### Measured, not asserted

The generator stamps every injected incident with an `attack_label` the detectors never see.
`mlpipeline.evaluate` replays stored windows and anomalies against those labels:

```bash
make evaluate
```

Measured on this machine over a 24-hour replay (584 one-minute windows, 296 of them containing an
injected incident, severity ≥ medium). The traffic is regenerated on every run, so your figures will
differ by a few points — the numbers below are one representative run, not a fixed benchmark:

```
confusion         : TP=267  FP=34  FN=29  TN=246
precision         : 0.887
recall            : 0.902
F1                : 0.894
false-positive rt : 0.121
incident recall   : 0.908   (59 of 65 incidents caught)
detection latency : 0.3 min

scenario               per-minute     per-incident
data_scraping          1.00 (41/41)   1.00 (7/7)
ddos_burst             1.00 (19/19)   1.00 (5/5)
geo_shift              1.00 (11/11)   1.00 (2/2)
service_outage         1.00 (22/22)   1.00 (5/5)
error_spike            0.96 (23/24)   0.86 (6/7)
bot_surge              0.95 (20/21)   0.89 (8/9)
endpoint_scan          0.89 (39/44)   0.90 (9/10)
credential_stuffing    0.86 (19/22)   0.71 (5/7)
latency_degradation    0.79 (73/92)   0.92 (12/13)
```

**Both recall figures are reported, because minute-level recall is misleading on its own.** A latency
degradation that ramps over 25% of its duration has minutes at each end that are *labelled* as
incident but where the metric is still at baseline — there is genuinely nothing to detect. Per-minute
recall punishes those; per-incident recall answers the question an operator actually asks: *was it
caught, and how fast?* `latency_degradation` shows the gap clearly: 0.79 per minute, 0.92 per
incident.

The report is written to MongoDB and rendered on the Anomalies page, so the dashboard is honest about
how good its own detection is.

### How it got there

The first run of this evaluation scored **precision 0.605, F1 0.700**, with 7,988 detections over the
same period. Six rounds of measured tuning — each driven by inspecting what the false positives
actually were — brought it to F1 0.894:

| Fix | Why it was wrong | Effect |
|---|---|---|
| Volume floor on the model-only IP catch-all | In a heavy-tailed population *being in the tail is normal*; 5-request clients with a 100% error ratio were scored critical | `suspicious_ip` precision 50% → 99% |
| Mahalanobis given its own detector identity | It reported itself as `isolation_forest`, so fusion counted two correlated views of the same statistic as independent agreement and inflated borderline scores | removed a systematic score inflation |
| Materiality gates (sample size + effect size) | A p95 moving 60 ms → 195 ms is a 12σ event and irrelevant; an error rate over 22 requests supports no claim at all | `slow_endpoint` 1,635 → 43 detections, precision 10% → 98% |
| Trend-residual detector, and share-based endpoint/geo signals | A rolling median lags a rising series, so the whole morning ramp reads as a continuous spike; and when total traffic doubles, every endpoint and country doubles with it | `traffic_spike` precision 12% → 81% |
| Error detection keyed on 5xx, not total errors | 401s from auth-protected routes are the service working correctly — anonymous clients give `/api/v1/cart` a permanent double-digit 4xx rate | removed 140 false positives in one change |
| Share materiality re-calibrated against real data | The first cut demanded a 5-point share gain and treated a *new* origin's baseline as its current share — together those suppressed every injected `geo_shift`, which measure only 1-4% of traffic | `geo_shift` recall 0.00 → 1.00 |

The last row is the one worth dwelling on: the fix for a false-positive problem quietly created a
false-*negative* problem, and only re-measuring caught it. Tuning a detector without re-running the
evaluation is guessing.

Every one of those is pinned by a regression test in `tests/test_regressions.py`, including the
negative cases (the volume floor must not suppress a real attacker; the 5xx rule must still catch a
real error spike).

---

## The dashboard

Ten views, one global time range, dark-first.

| View | What it is for |
|---|---|
| **Overview** | Is everything fine right now? KPI strip, traffic with detections shaded onto it, live feed |
| **Traffic** | Volume, composition, shape indicators, weekly heatmap, k-means operating profiles, seasonal baseline |
| **Endpoints** | Per-route table with sparklines and latency budget bars; drill-down drawer per route |
| **Sessions** | Gap-based sessions, entry/exit points, conversion, bot share |
| **Log Explorer** | Query language, facets, match histogram, virtualised results, live Kafka tail |
| **Geography** | Three.js origin globe, per-country table, flagged sources |
| **Anomalies** | Detection feed with full evidence; measured detection quality |
| **Incidents** | Correlated records with timeline, impact and acknowledgement |
| **Alerts** | What actually paged, and rule state |
| **System Health** | Ingest lag, Kafka lag, per-query Spark progress, storage, models |

Design notes: a validated categorical palette (every adjacent pair clears colour-vision-deficiency
separation and 3:1 contrast in both themes), reserved status hues that never double as series colours,
severity always paired with a label or icon, one y-axis per chart, tabular figures everywhere numbers
align, and motion limited to purposeful transitions that respect `prefers-reduced-motion`.

---

## Project layout

```
BDA-Project/
├── services/
│   ├── common/            # shared library — the backbone
│   │   └── loglens_common/
│   │       ├── config.py       env-driven settings, stdlib only
│   │       ├── schemas.py      log/anomaly/incident contracts + Spark StructType
│   │       ├── mongo.py        connections, index spec, bulk helpers, runtime config
│   │       ├── aggregation.py  reference (pandas) implementation of every metric doc
│   │       ├── detectors.py    the statistical detector library
│   │       ├── analyzers.py    domain analyzers → explained anomalies
│   │       ├── kafka_io.py     topic bootstrap, tuned producer, lag reporting
│   │       └── timeutil.py     range parsing and bucketing
│   ├── generator/         # synthetic traffic + attack scenarios + backfill
│   ├── streaming/         # Spark application (transforms, sinks, history cache)
│   ├── ml/                # training and evaluation pipelines
│   ├── worker/            # correlation, alerting, health, retention, retraining
│   └── api/               # FastAPI: routers, query language, serialisation, WS
├── frontend/              # React + TypeScript + Tailwind dashboard
│   └── src/{charts,components,hooks,layout,lib,pages,styles}
├── docker/                # Dockerfiles, nginx config, mongo replica-set init
├── grafana/               # provisioned datasource + dashboard
├── scripts/               # bootstrap for bash and PowerShell
├── tests/                 # detector and end-to-end pipeline tests
├── docker-compose.yml
└── Makefile
```

---

## Configuration

Everything is environment-driven through a single `.env` (see `.env.example` for the annotated list).
The knobs you are most likely to touch:

| Variable | Default | Effect |
|---|---|---|
| `GEN_BASE_RPS` | `180` | traffic volume at peak-normalised load |
| `GEN_SCENARIOS` | `true` | automatic incident injection |
| `KAFKA_PARTITIONS` | `6` | ingestion parallelism |
| `SPARK_SHUFFLE_PARTITIONS` | `6` | task granularity for the aggregations |
| `SPARK_TRIGGER_INTERVAL` | `10 seconds` | micro-batch cadence |
| `SPARK_WATERMARK_DELAY` | `2 minutes` | how long late events are accepted |
| `WINDOW_METRIC` | `1 minute` | tumbling window size |
| `SESSION_GAP` | `15 minutes` | session inactivity timeout |
| `MONGO_RAW_TTL_SECONDS` | `86400` | raw log retention |
| `DET_*` | see `.env.example` | detector thresholds (also editable live in **Settings**) |

Detector thresholds edited in the UI are stored in MongoDB and picked up by the running Spark
detectors within ~30 seconds — no redeploy, no restart.

**Scaling up.** For a heavier demonstration: raise `GEN_BASE_RPS` to 1000–3000, raise
`KAFKA_PARTITIONS` and `SPARK_SHUFFLE_PARTITIONS` to match, give the worker more cores
(`SPARK_WORKER_CORES`), and add Spark workers with
`docker compose up -d --scale spark-worker=3`.

---

## API

Full OpenAPI at `/docs`. Every endpoint accepts `range` (`15m`, `1h`, `24h`, `7d`) or explicit
`start`/`end`.

| Endpoint | Purpose |
|---|---|
| `GET /api/overview` | KPIs with period-over-period deltas and sparkline series |
| `GET /api/metrics/timeseries` | bucketed series for any metric in the catalog |
| `GET /api/metrics/status-breakdown` | requests per status class over time |
| `GET /api/metrics/baseline` | seasonal expected band |
| `GET /api/traffic/patterns` | weekly heatmap and k-means operating profiles |
| `GET /api/logs` | log search (query language + filters + pagination) |
| `GET /api/logs/histogram` · `/facets` · `/{id}` | histogram, facets, single request with session trail |
| `GET /api/anomalies` · `/summary` · `/{id}` | detection feed, rollups, full evidence |
| `PATCH /api/anomalies/{id}` | acknowledge / resolve / suppress |
| `GET /api/incidents` · `/{id}` · `PATCH` | correlated incidents |
| `GET /api/alerts` · `PATCH /api/alerts/{id}` | fired alerts |
| `GET /api/endpoints` · `/detail` · `GET /api/services` | performance analytics |
| `GET /api/sessions` · `/{id}` | session analytics |
| `GET /api/ips` · `/{ip}` · `GET /api/security/summary` | source intelligence |
| `GET /api/geo` · `/timeseries` · `/points` | geography |
| `GET /api/system/health` · `/pipeline` · `/stats` · `/models` | platform self-monitoring |
| `GET|PUT|DELETE /api/config/thresholds` | live detector tuning |
| `GET|PUT|DELETE /api/config/alert-rules` | alert rules |
| `GET|POST /api/system/scenarios` | list and inject incident scenarios |
| `WS /ws/live` | metric ticks and fresh anomalies |
| `WS /ws/logs` | live log tail from Kafka, filtered and rate-limited |

---

## Development

```bash
make install-dev     # install the shared package and service requirements
make test            # detector + end-to-end pipeline tests (no infra needed)

make dev-api         # API with reload, against the compose infrastructure
make dev-frontend    # Vite dev server on :5173, proxying /api and /ws
```

The test suite deliberately runs without Kafka, Spark or MongoDB: the parts that must be correct —
the statistics, the metric definitions, the analyzers, the generator's shape — are pure functions.

```bash
python -m pytest tests/ -v
```

Useful one-liners:

```bash
make attack          # inject a DDoS burst
make slow            # inject latency degradation
make outage          # inject a service outage
make health          # platform health as JSON
make logs-streaming  # follow the Spark application
make rescore         # re-run detection over stored history after changing thresholds
make evaluate        # score detection quality against ground truth
```

`make rescore` is the tuning loop: change a threshold in **Settings** (or a detector in code),
re-score the stored history, and re-run the evaluation. Because the expensive part of a backfill is
simulating and aggregating the traffic — and that output is already in MongoDB — the whole cycle is
minutes rather than a full re-simulation. Every tuning decision in the table above was made this way.

---

## Troubleshooting

**Dashboard is empty.** Check `make health`. If `pipeline_lag_seconds` is large or null, Spark has not
produced a window yet — the first micro-batch takes ~1 minute after startup. `make logs-streaming`
shows query progress.

**No history / seasonal baseline missing.** Run `make backfill` (then `make train`). Detection works
without it, but the seasonal detector stays neutral until it has previous days to compare against.

**Spark cannot fetch the Kafka connector.** The image pre-warms an Ivy cache at build time; if the
build ran offline the first `spark-submit` resolves it from Maven and needs network. Check
`docker compose logs streaming`.

**Kafka fails to start after `docker compose down -v`.** Cluster ID mismatch from a stale volume:
`docker compose down -v` again, then `docker compose up -d`.

**Ports already in use.** Change the host-side port mappings in `docker-compose.yml`; every service
reads its own port from the environment.

**Memory pressure.** Lower `GEN_BASE_RPS`, raise `SPARK_TRIGGER_INTERVAL`, and reduce
`SPARK_WORKER_MEMORY`. Eight stateful queries on a 4 GB machine will be tight.

---

## Notes on scope

The traffic is synthetic — deliberately. Building it that way means the platform can be demonstrated
end to end on a laptop, and, more importantly, that detection quality can be *measured* against ground
truth instead of asserted. Point the Kafka producer at real access logs (any JSON matching
`services/common/loglens_common/schemas.py`) and nothing downstream changes.
