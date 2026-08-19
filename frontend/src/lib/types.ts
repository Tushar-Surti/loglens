/** Shapes returned by the LogLens API. Kept close to the server contracts. */

export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical'
export type HealthStatus = 'healthy' | 'degraded' | 'critical' | 'stale' | 'unknown' | 'ok' | 'stalled' | 'no_data'

export interface RangeMeta {
  start: string
  end: string
  label: string
  bucket_seconds: number
}

export interface Point {
  t: string
  v: number
}

export interface Totals {
  requests: number
  rps: number
  peak_rps: number
  errors: number
  errors_4xx: number
  errors_5xx: number
  error_rate: number
  server_error_rate: number
  availability: number
  bytes_sent: number
  p50_response_time: number
  p95_response_time: number
  p99_response_time: number
  avg_response_time: number
  apdex: number
  unique_ips: number
  unique_sessions: number
  bot_ratio: number
  cache_hit_rate: number
  windows: number
}

export interface Overview {
  range: RangeMeta
  totals: Totals
  previous: Totals
  deltas: Record<string, number | null>
  series: Record<string, Point[]>
  anomalies: { by_severity: Partial<Record<Severity, number>>; total: number }
  incidents: { open: number; top: Incident[] }
  alerts: { firing_last_hour: number }
}

export interface MetricSeries {
  label: string
  unit: string
  points: Point[]
}

export interface TimeseriesResponse {
  range: RangeMeta
  metrics: Record<string, MetricSeries>
}

export interface DetectorContribution {
  detector: string
  score: number
  triggered: boolean
  reason: string
  evidence: Record<string, unknown>
}

export interface Anomaly {
  anomaly_id: string
  type: string
  type_label: string
  entity_type: string
  entity: string
  service?: string | null
  score: number
  confidence: number
  severity: Severity
  detector: string
  detectors: string[]
  agreement: number
  reason: string
  headline: string
  window_start: string
  window_end: string
  detected_at: string
  metrics: Record<string, number | string | null>
  contributing?: DetectorContribution[]
  evidence?: Record<string, Record<string, unknown>>
  status: string
  incident_id: string | null
  ground_truth: string | null
}

export interface Incident {
  incident_id: string
  title: string
  family: string
  family_label: string
  type: string
  entity_type: string
  entity: string
  severity: Severity
  score: number
  peak_score: number
  status: 'open' | 'acknowledged' | 'resolved'
  started_at: string
  last_seen_at: string
  resolved_at?: string | null
  anomaly_count: number
  detectors: string[]
  types: string[]
  entities: string[]
  summary: string
  timeline: { at: string; type: string; severity: Severity; score: number; entity: string; reason: string }[]
  impact: {
    requests?: number
    peak_error_rate?: number
    peak_p95_ms?: number
    peak_rps?: number
    duration_minutes?: number
  }
  ground_truth?: string | null
}

export interface Alert {
  alert_id: string
  rule_id: string
  rule_name: string
  severity: Severity
  score: number
  title: string
  message: string
  type: string
  entity: string
  entity_type: string
  incident_id: string | null
  channels: string[]
  created_at: string
  status: string
  acknowledged: boolean
  webhook_status?: string
}

export interface LogEvent {
  event_id: string
  timestamp: string
  ip: string
  method: string
  path: string
  endpoint: string
  status: number
  status_class?: string
  bytes_sent: number
  response_time_ms: number
  upstream_time_ms?: number
  user_agent: string
  referrer?: string
  user_id?: string | null
  session_id?: string
  country?: string
  country_name?: string
  city?: string
  service?: string
  host?: string
  region?: string
  device?: string
  browser?: string
  os?: string
  is_bot?: boolean
  cache_status?: string
  asn?: string
  org?: string
  attack_label?: string | null
}

export interface EndpointRow {
  endpoint: string
  method: string
  service: string
  requests: number
  traffic_share: number
  errors_4xx: number
  errors_5xx: number
  error_rate: number
  p50_response_time: number
  p95_response_time: number
  p99_response_time: number
  max_response_time: number
  avg_response_time: number
  apdex: number
  unique_ips: number
  cache_hit_rate: number
  bytes_sent: number
  latency_budget: number
  sparkline?: { t: string; requests: number; p95: number; error_rate: number }[]
}

export interface IpRow {
  ip: string
  requests: number
  error_ratio: number
  not_found_ratio: number
  auth_fail_count: number
  unique_paths: number
  unique_user_agents: number
  bytes_sent: number
  peak_rps: number
  avg_response_time: number
  burstiness: number
  path_entropy: number
  bot_ratio: number
  country: string
  asn: string
  org: string
  first_seen: string
  last_seen: string
  threat_score: number
  severity: Severity | null
  anomaly_count: number
  anomaly_types: string[]
  ground_truth?: string | null
}

export interface GeoRow {
  country: string
  country_name: string
  requests: number
  share: number
  unique_ips: number
  unique_sessions: number
  error_rate: number
  p95_response_time: number
  avg_response_time: number
  bytes_sent: number
  lat: number
  lon: number
  threat_score: number
  anomaly_count: number
  ground_truth?: string | null
}

export interface GeoPoint {
  country: string
  country_name: string
  city: string
  lat: number
  lon: number
  requests: number
  weight: number
  error_rate: number
  avg_response_time: number
  hostile: boolean
}

export interface SessionRow {
  session_id: string
  session_start: string
  session_end: string
  duration_seconds: number
  requests: number
  requests_per_minute: number
  unique_endpoints: number
  error_count: number
  bytes_sent: number
  user_id: string | null
  ip: string
  country: string
  device: string
  browser: string
  is_bot: boolean
  entry_endpoint: string
  exit_endpoint: string
  converted: boolean
  avg_response_time: number
}

export interface ComponentHealth {
  status: HealthStatus
  age_seconds: number | null
  last_seen: string | null
  details: Record<string, any>
}

export interface SystemHealth {
  status: HealthStatus
  updated_at: string | null
  components: Record<string, ComponentHealth>
  pipeline_lag_seconds: number | null
  details: {
    kafka?: { status: string; total_lag?: number; topics?: Record<string, { lag: number; partitions: number; log_end_offset: number }> }
    mongodb?: { status: string; collections?: Record<string, number>; storage_mb?: number; data_mb?: number; index_mb?: number }
    pipeline?: { status: string; lag_seconds?: number; rps?: number; requests_last_window?: number }
    throughput?: { events_per_second: number; requests?: number; avg_ingest_latency_ms?: number }
    degraded_components?: string[]
  }
}

export interface SparkQuery {
  active: boolean
  batch_id?: number
  input_rows_per_second?: number
  processed_rows_per_second?: number
  num_input_rows?: number
  batch_duration_ms?: number
  state_rows?: number
  status?: string
}

export interface PipelineTelemetry {
  spark_queries: Record<string, SparkQuery>
  spark_updated_at: string | null
  generator: {
    active?: { scenario_id: string; type: string; label: string; description: string; ends_at: number; progress: number; envelope: number; source: string }[]
    recent?: { scenario_id: string; type: string; started_at: number; ends_at: number }[]
    stats?: Record<string, number>
  }
  generator_updated_at: string | null
  backfill: { completed_at?: string; hours?: number; events?: number; windows?: number; anomalies?: number } | null
  batches_15m: { query: string; batches: number; rows: number; anomalies: number; avg_duration_ms: number; max_duration_ms: number }[]
  throughput: { t: string; rows: number; duration_ms: number }[]
  config: Record<string, any>
}

export interface ThresholdConfig {
  values: Record<string, number>
  defaults: Record<string, number>
  overrides: Record<string, number>
  bounds: Record<string, { min: number; max: number }>
  meta: Record<string, { label: string; help: string }>
  updated_at: string | null
}

export interface AlertRule {
  rule_id: string
  name: string
  description: string
  enabled: boolean
  match: { severity_at_least?: Severity; types?: string[]; min_score?: number; entity_types?: string[] }
  channels: string[]
  cooldown_seconds: number
  builtin?: boolean
}

export interface DetectionQuality {
  evaluated_at: string
  window_hours: number
  min_severity: string
  minutes_evaluated: number
  attack_minutes: number
  confusion: { tp: number; fp: number; fn: number; tn: number }
  precision: number
  recall: number
  f1: number
  false_positive_rate: number
  mean_detection_latency_minutes: number | null
  per_scenario: Record<string, { true_positives: number; false_negatives: number; recall: number }>
  detector_usage: Record<string, number>
}

export interface ScenarioCatalogEntry {
  type: string
  label: string
  description: string
  weight: number
}

export interface LiveTick {
  type: 'tick' | 'hello'
  at: string
  current: Record<string, any> | null
  series: Record<string, any>[]
  anomalies: Anomaly[]
  open_incidents: number
  health: HealthStatus
  connections: { live: number; tail: number }
}
