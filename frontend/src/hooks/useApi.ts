/** React Query bindings for every endpoint the dashboard uses. */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query'

import { api, type QueryValue } from '@/lib/api'
import { refetchInterval, useUi } from '@/lib/store'
import type {
  Alert,
  AlertRule,
  Anomaly,
  EndpointRow,
  GeoPoint,
  GeoRow,
  Incident,
  IpRow,
  LogEvent,
  Overview,
  PipelineTelemetry,
  RangeMeta,
  ScenarioCatalogEntry,
  SessionRow,
  SystemHealth,
  ThresholdConfig,
  TimeseriesResponse,
} from '@/lib/types'

type Params = Record<string, QueryValue>

/** Shared options: every list view follows the same freshness policy. */
function live<T>(key: unknown[], path: string, params?: Params, options?: Partial<UseQueryOptions<T>>) {
  const { range, live: isLive } = useUi()
  return useQuery<T>({
    queryKey: [...key, range, params],
    queryFn: () => api.get<T>(path, { range, ...params }),
    refetchInterval: refetchInterval(range, isLive),
    staleTime: 3_000,
    placeholderData: (previous) => previous,
    retry: 1,
    ...(options as any),
  })
}

// ── Overview & metrics ───────────────────────────────────────────────────────
export const useOverview = () => live<Overview>(['overview'], '/overview')

export const useTimeseries = (metrics: string[], params?: Params) =>
  live<TimeseriesResponse>(['timeseries', metrics.join(',')], '/metrics/timeseries', {
    metrics: metrics.join(','),
    ...params,
  })

export const useStatusBreakdown = () =>
  live<{ range: RangeMeta; classes: string[]; points: Record<string, any>[] }>(
    ['status-breakdown'],
    '/metrics/status-breakdown',
  )

export const useBaseline = (metric: string) =>
  useQuery({
    queryKey: ['baseline', metric],
    queryFn: () =>
      api.get<{ metric: string; available: boolean; day_type: string; buckets: any[] }>('/metrics/baseline', {
        metric,
      }),
    staleTime: 300_000,
  })

export const useTrafficPatterns = () =>
  live<{ range: RangeMeta; profiles: any[]; heatmap: any[]; profiles_updated_at: string | null }>(
    ['traffic-patterns'],
    '/traffic/patterns',
  )

// ── Logs ─────────────────────────────────────────────────────────────────────
export const useLogs = (params: Params, enabled = true) =>
  live<{
    items: LogEvent[]
    total: number | null
    total_is_capped?: boolean
    count: number
    has_more: boolean
    range: RangeMeta
  }>(
    ['logs'],
    '/logs',
    params,
    { enabled },
  )

export const useLogHistogram = (params: Params) =>
  live<{ range: RangeMeta; classes: string[]; points: Record<string, any>[] }>(['log-histogram'], '/logs/histogram', params)

export const useLogFacets = (params: Params) =>
  live<{ facets: Record<string, { value: string; count: number }[]> }>(['log-facets'], '/logs/facets', params)

export const useLogDetail = (eventId: string | null) =>
  useQuery({
    queryKey: ['log', eventId],
    queryFn: () =>
      api.get<{ event: LogEvent; session_events?: LogEvent[]; ip_activity?: any[] }>(`/logs/${eventId}`),
    enabled: Boolean(eventId),
    staleTime: 60_000,
  })

// ── Anomalies, incidents, alerts ─────────────────────────────────────────────
export const useAnomalies = (params?: Params) =>
  live<{ items: Anomaly[]; total: number; range: RangeMeta }>(['anomalies'], '/anomalies', params)

export const useAnomalySummary = () =>
  live<{
    range: RangeMeta
    by_type: { type: string; label: string; count: number; max_score: number; avg_score: number }[]
    by_severity: Record<string, number>
    timeline: Record<string, any>[]
    top_entities: { entity: string; entity_type: string; count: number; max_score: number }[]
    detection_quality: any
  }>(['anomaly-summary'], '/anomalies/summary')

export const useAnomaly = (id: string | null) =>
  useQuery({
    queryKey: ['anomaly', id],
    queryFn: () =>
      api.get<{ anomaly: Anomaly; incident?: Incident; context: any[]; sample_logs?: LogEvent[] }>(
        `/anomalies/${id}`,
      ),
    enabled: Boolean(id),
  })

export const useIncidents = (params?: Params) =>
  live<{ items: Incident[]; total: number; by_status: Record<string, number>; range: RangeMeta }>(
    ['incidents'],
    '/incidents',
    params,
  )

export const useIncident = (id: string | undefined) =>
  useQuery({
    queryKey: ['incident', id],
    queryFn: () =>
      api.get<{ incident: Incident; anomalies: Anomaly[]; alerts: Alert[]; metrics: any[] }>(`/incidents/${id}`),
    enabled: Boolean(id),
    refetchInterval: 15_000,
  })

export const useAlerts = (params?: Params) =>
  live<{ items: Alert[]; total: number; range: RangeMeta }>(['alerts'], '/alerts', params)

export function useUpdateIncident() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; status: string; resolution?: string; acknowledged_by?: string }) =>
      api.patch(`/incidents/${id}`, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['incidents'] })
      client.invalidateQueries({ queryKey: ['incident'] })
      client.invalidateQueries({ queryKey: ['overview'] })
    },
  })
}

export function useUpdateAnomaly() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; status: string; note?: string }) =>
      api.patch(`/anomalies/${id}`, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['anomalies'] })
      client.invalidateQueries({ queryKey: ['anomaly'] })
    },
  })
}

export function useUpdateAlert() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; acknowledged?: boolean; status?: string }) =>
      api.patch(`/alerts/${id}`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: ['alerts'] }),
  })
}

// ── Performance ──────────────────────────────────────────────────────────────
export const useEndpoints = (params?: Params) =>
  live<{ items: EndpointRow[]; range: RangeMeta }>(['endpoints'], '/endpoints', params)

export const useEndpointDetail = (endpoint: string | null, method?: string) => {
  const { range } = useUi()
  return useQuery({
    queryKey: ['endpoint-detail', endpoint, method, range],
    queryFn: () =>
      api.get<{ summary: any; series: any[]; anomalies: Anomaly[]; slowest_requests: LogEvent[]; recent_errors: LogEvent[] }>(
        '/endpoints/detail',
        { endpoint, method, range },
      ),
    enabled: Boolean(endpoint),
    refetchInterval: 20_000,
  })
}

export const useServices = () => live<{ items: any[]; range: RangeMeta }>(['services'], '/services')

export const useSessions = (params?: Params) =>
  live<{ items: SessionRow[]; summary: any; entry_points: any[]; exit_points: any[]; range: RangeMeta }>(
    ['sessions'],
    '/sessions',
    params,
  )

// ── Security & geography ─────────────────────────────────────────────────────
export const useIps = (params?: Params) => live<{ items: IpRow[]; range: RangeMeta }>(['ips'], '/ips', params)

export const useIpDetail = (ip: string | null) => {
  const { range } = useUi()
  return useQuery({
    queryKey: ['ip-detail', ip, range],
    queryFn: () =>
      api.get<{ summary: any; series: any[]; anomalies: Anomaly[]; recent_logs: LogEvent[]; top_endpoints: any[] }>(
        `/ips/${ip}`,
        { range },
      ),
    enabled: Boolean(ip),
  })
}

export const useSecuritySummary = () =>
  live<{ top_attackers: any[]; attack_types: Record<string, number>; high_severity_sources: number; range: RangeMeta }>(
    ['security-summary'],
    '/security/summary',
  )

export const useGeo = () => live<{ items: GeoRow[]; total_requests: number; range: RangeMeta }>(['geo'], '/geo')

export const useGeoPoints = () => live<{ points: GeoPoint[]; range: RangeMeta }>(['geo-points'], '/geo/points')

export const useGeoTimeseries = (countries?: string[]) =>
  live<{ countries: string[]; points: Record<string, any>[]; range: RangeMeta }>(['geo-timeseries'], '/geo/timeseries', {
    countries,
  })

// ── System ───────────────────────────────────────────────────────────────────
export const useSystemHealth = () =>
  useQuery({
    queryKey: ['system-health'],
    queryFn: () => api.get<SystemHealth>('/system/health'),
    refetchInterval: 10_000,
    staleTime: 5_000,
  })

export const usePipeline = () =>
  useQuery({
    queryKey: ['pipeline'],
    queryFn: () => api.get<PipelineTelemetry>('/system/pipeline'),
    refetchInterval: 10_000,
  })

export const useSystemStats = () =>
  useQuery({
    queryKey: ['system-stats'],
    queryFn: () => api.get<{ collections: Record<string, number>; storage_mb: number; data_mb: number; index_mb: number }>('/system/stats'),
    refetchInterval: 30_000,
  })

export const useModels = () =>
  useQuery({
    queryKey: ['models'],
    queryFn: () => api.get<{ models: any[]; detection_quality: any; last_training: any; traffic_profiles: any[] }>('/system/models'),
    refetchInterval: 60_000,
  })

export const useThresholds = () =>
  useQuery({ queryKey: ['thresholds'], queryFn: () => api.get<ThresholdConfig>('/config/thresholds') })

export function useSaveThresholds() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (values: Record<string, number>) => api.put('/config/thresholds', values),
    onSuccess: () => client.invalidateQueries({ queryKey: ['thresholds'] }),
  })
}

export function useResetThresholds() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.delete('/config/thresholds'),
    onSuccess: () => client.invalidateQueries({ queryKey: ['thresholds'] }),
  })
}

export const useAlertRules = () =>
  useQuery({ queryKey: ['alert-rules'], queryFn: () => api.get<{ items: AlertRule[] }>('/config/alert-rules') })

export function useSaveAlertRule() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ ruleId, ...body }: { ruleId: string } & Partial<AlertRule>) =>
      api.put(`/config/alert-rules/${ruleId}`, body),
    onSuccess: () => client.invalidateQueries({ queryKey: ['alert-rules'] }),
  })
}

export const useScenarios = () =>
  useQuery({
    queryKey: ['scenarios'],
    queryFn: () =>
      api.get<{ catalog: ScenarioCatalogEntry[]; active: any[]; recent: any[]; generator: Record<string, number> }>(
        '/system/scenarios',
      ),
    refetchInterval: 5_000,
  })

export function useInjectScenario() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { type: string; intensity?: number; duration?: number }) =>
      api.post('/system/scenarios', body),
    onSuccess: () => client.invalidateQueries({ queryKey: ['scenarios'] }),
  })
}
