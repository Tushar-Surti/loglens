/**
 * Overview — the "is everything fine right now?" view.
 *
 * Ordered by the question an operator asks first: headline numbers, then the
 * traffic shape with detected incidents drawn onto it, then what is failing and
 * where.
 */

import { ArrowUpRight, ShieldAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { BarList } from '@/charts/BarList'
import { StackedStatusChart } from '@/charts/StackedStatusChart'
import { TimeSeriesChart, type AnomalyBand } from '@/charts/TimeSeriesChart'
import { useChartTheme } from '@/charts/palette'
import { GroundTruthBadge, SeverityBadge, SeverityDot } from '@/components/Badges'
import { SegmentedControl } from '@/components/Controls'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, Panel, PageTransition, QueryBoundary } from '@/components/Panel'
import { AnimatedNumber, StatStrip, StatTile } from '@/components/StatTile'
import {
  useAnomalies,
  useEndpoints,
  useGeo,
  useOverview,
  useStatusBreakdown,
  useTimeseries,
} from '@/hooks/useApi'
import { useLiveTicks } from '@/hooks/useLive'
import { compact, ms, number, percent, relativeTime, truncate } from '@/lib/format'
import { useUi } from '@/lib/store'

const PRIMARY_METRICS = [
  { value: 'rps', label: 'Throughput' },
  { value: 'error_rate', label: 'Errors' },
  { value: 'p95_response_time', label: 'Latency' },
] as const

type PrimaryMetric = (typeof PRIMARY_METRICS)[number]['value']

const SEVERITY_RANK: Record<string, number> = { info: 0, low: 1, medium: 2, high: 3, critical: 4 }

export function Overview() {
  const theme = useChartTheme()
  const live = useUi((state) => state.live)
  const [metric, setMetric] = useState<PrimaryMetric>('rps')

  const overview = useOverview()
  const status = useStatusBreakdown()
  const endpoints = useEndpoints({ limit: 8, sort: 'requests', with_sparklines: false })
  const geo = useGeo()
  const anomalies = useAnomalies({ severity: 'medium', limit: 60 })
  const { tick, recentAnomalies } = useLiveTicks(live)

  const totals = overview.data?.totals
  const series = overview.data?.series
  const bucket = overview.data?.range.bucket_seconds ?? 60

  // Anomaly windows drawn behind the traffic line: an incident and the metric
  // that produced it belong in the same glance.
  //
  // Every entity type counts, not just platform-level ones. A DDoS often fires
  // twenty per-IP detections without tripping the global volume detector, and
  // shading only `global` left the chart looking clean during an active attack.
  // Windows are collapsed to one band each, keeping the worst severity, so 170
  // detections become a handful of readable regions rather than 170 stacked
  // translucent rectangles.
  const bands = useMemo<AnomalyBand[]>(() => {
    const worst = new Map<number, AnomalyBand>()
    for (const item of anomalies.data?.items ?? []) {
      const at = new Date(item.window_start).getTime()
      const existing = worst.get(at)
      if (!existing || SEVERITY_RANK[item.severity] > SEVERITY_RANK[existing.severity]) {
        worst.set(at, {
          start: item.window_start,
          end: item.window_end,
          severity: item.severity,
          label: item.type_label,
        })
      }
    }

    // Merge windows that touch into one region. Drawing a translucent rectangle
    // per minute stacks the alpha where they overlap, turning a five-minute
    // incident into an opaque block with darker seams. One band per contiguous
    // run reads as the period it actually was.
    const ordered = [...worst.entries()].sort((a, b) => a[0] - b[0]).map(([, band]) => band)
    const merged: AnomalyBand[] = []
    for (const band of ordered) {
      const previous = merged[merged.length - 1]
      const touches = previous && new Date(band.start).getTime() <= new Date(previous.end).getTime()
      if (touches) {
        previous.end = band.end
        if (SEVERITY_RANK[band.severity] > SEVERITY_RANK[previous.severity]) {
          previous.severity = band.severity
          previous.label = band.label
        }
      } else {
        merged.push({ ...band })
      }
    }
    return merged
  }, [anomalies.data])

  const chartData = useMemo(() => {
    const points = series?.[metric] ?? []
    return points.map((point) => ({ t: point.t, value: point.v }))
  }, [series, metric])

  const metricMeta = {
    rps: { label: 'Requests / sec', format: (value: number) => `${number(value, 1)}/s`, color: theme.series[0] },
    error_rate: { label: 'Error rate', format: (value: number) => percent(value, 2), color: theme.status.critical },
    p95_response_time: { label: 'p95 latency', format: (value: number) => ms(value), color: theme.series[3] },
  }[metric]

  // The live socket reports anomalies by *detection* time, so after a restart it
  // replays detections for old windows. Showing those next to a KPI that counts
  // by window time made the panel contradict the number above it. Both are
  // filtered to the selected range; ordering stays newest-window-first.
  const feed = useMemo(() => {
    const from = new Date(overview.data?.range.start ?? 0).getTime()
    const to = new Date(overview.data?.range.end ?? Date.now()).getTime()
    const merged = new Map<string, (typeof recentAnomalies)[number]>()
    for (const anomaly of [...recentAnomalies, ...(anomalies.data?.items ?? [])]) {
      const at = new Date(anomaly.window_start).getTime()
      if (at >= from && at <= to) merged.set(anomaly.anomaly_id, anomaly)
    }
    return [...merged.values()].sort(
      (a, b) => new Date(b.window_start).getTime() - new Date(a.window_start).getTime(),
    )
  }, [recentAnomalies, anomalies.data, overview.data])

  return (
    <PageTransition>
      <PageHeader
        title="Overview"
        description="Live platform health across traffic, errors, latency and detected incidents."
        actions={
          <Link to="/anomalies" className="btn btn-sm">
            <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
            Anomaly feed
          </Link>
        }
      />

      <StatStrip className="mb-4">
        <StatTile
          label="Requests"
          value={<AnimatedNumber value={totals?.requests ?? 0} format={compact} />}
          delta={overview.data?.deltas.requests}
          sparkline={(series?.rps ?? []).map((point) => point.v)}
          footer={`${number(totals?.rps ?? 0, 1)} rps average · peak ${number(totals?.peak_rps ?? 0, 0)}`}
          hint="Total requests processed in the selected range"
        />
        <StatTile
          label="Error rate"
          value={percent(totals?.error_rate ?? 0, 2)}
          delta={overview.data?.deltas.error_rate}
          invertDelta
          tone={(totals?.error_rate ?? 0) > 0.08 ? 'critical' : (totals?.error_rate ?? 0) > 0.03 ? 'warn' : 'default'}
          sparkline={(series?.error_rate ?? []).map((point) => point.v)}
          sparklineColor={theme.status.critical}
          footer={`${compact(totals?.errors_4xx ?? 0)} client · ${compact(totals?.errors_5xx ?? 0)} server`}
        />
        <StatTile
          label="p95 latency"
          value={ms(totals?.p95_response_time ?? 0)}
          delta={overview.data?.deltas.p95_response_time}
          invertDelta
          sparkline={(series?.p95_response_time ?? []).map((point) => point.v)}
          sparklineColor={theme.series[3]}
          footer={`p50 ${ms(totals?.p50_response_time ?? 0)} · p99 ${ms(totals?.p99_response_time ?? 0)}`}
        />
        <StatTile
          label="Apdex"
          value={(totals?.apdex ?? 1).toFixed(3)}
          delta={overview.data?.deltas.apdex}
          tone={(totals?.apdex ?? 1) < 0.85 ? 'warn' : 'good'}
          footer="Satisfied under 500 ms"
          hint="Apdex with a 500 ms target: satisfied + tolerating/2 over total"
        />
        <StatTile
          label="Active sessions"
          value={compact(totals?.unique_sessions ?? 0)}
          delta={overview.data?.deltas.unique_sessions}
          sparkline={(series?.unique_sessions ?? []).map((point) => point.v)}
          sparklineColor={theme.series[2]}
          footer={`${compact(totals?.unique_ips ?? 0)} unique source IPs`}
        />
        <StatTile
          label="Open incidents"
          value={overview.data?.incidents.open ?? 0}
          tone={(overview.data?.incidents.open ?? 0) > 0 ? 'critical' : 'good'}
          footer={`${overview.data?.anomalies.total ?? 0} anomalies · ${
            overview.data?.alerts.firing_last_hour ?? 0
          } alerts fired`}
        />
      </StatStrip>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title={metricMeta.label}
          subtitle={
            bands.length
              ? `${bands.length} detection period${bands.length === 1 ? '' : 's'} shaded`
              : 'no detections in range'
          }
          actions={
            <SegmentedControl
              options={PRIMARY_METRICS.map((item) => ({ value: item.value, label: item.label }))}
              value={metric}
              onChange={setMetric}
              size="sm"
              ariaLabel="Primary metric"
            />
          }
        >
          <QueryBoundary
            isLoading={overview.isLoading}
            error={overview.error}
            isEmpty={!chartData.length}
            onRetry={overview.refetch}
            height={280}
            emptyTitle="No traffic in this range"
            emptyMessage="Start the generator, or widen the time range."
          >
            <TimeSeriesChart
              data={chartData}
              series={[{ key: 'value', label: metricMeta.label, color: metricMeta.color }]}
              variant="area"
              height={280}
              bucketSeconds={bucket}
              anomalies={bands}
              syncId="overview"
              showLegend={false}
              formatValue={(_key, value) => metricMeta.format(value)}
              formatAxis={metric === 'error_rate' ? (value) => percent(value, 1) : compact}
            />
          </QueryBoundary>
        </Panel>

        <Panel
          title="Live detections"
          subtitle={live ? 'streaming' : 'paused'}
          dense
          bodyClassName="max-h-[352px] overflow-y-auto"
          actions={
            <Link to="/anomalies" className="text-2xs text-ink-3 transition-colors hover:text-ink">
              View all
            </Link>
          }
        >
          {feed.length === 0 ? (
            <EmptyState
              title="No anomalies detected"
              message="Detectors are running and the traffic looks normal. Use Simulate to inject an incident."
            />
          ) : (
            <ul className="divide-y divide-line">
              {feed.slice(0, 14).map((anomaly) => (
                <li key={anomaly.anomaly_id} className="animate-fade-up px-3 py-2.5">
                  <div className="flex items-start gap-2">
                    <SeverityDot severity={anomaly.severity} pulse={anomaly.severity === 'critical'} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium text-ink">{anomaly.headline || anomaly.type_label}</p>
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-ink-3">
                        <span className="font-mono">{truncate(anomaly.entity, 28)}</span>
                        <span>·</span>
                        <span>{relativeTime(anomaly.window_start)}</span>
                        <GroundTruthBadge label={anomaly.ground_truth} />
                      </p>
                    </div>
                    <span className="tnum shrink-0 text-xs font-semibold text-ink-2">{anomaly.score.toFixed(0)}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Response status composition" subtitle="stacked by class">
          <QueryBoundary
            isLoading={status.isLoading}
            error={status.error}
            isEmpty={!status.data?.points.length}
            onRetry={status.refetch}
            height={220}
          >
            <StackedStatusChart
              data={status.data?.points ?? []}
              classes={status.data?.classes ?? []}
              bucketSeconds={bucket}
              height={220}
              syncId="overview"
            />
          </QueryBoundary>
        </Panel>

        <Panel title="Latency distribution" subtitle="percentiles across all endpoints">
          <QueryBoundary
            isLoading={overview.isLoading}
            error={overview.error}
            isEmpty={!series?.p95_response_time?.length}
            height={220}
          >
            <LatencyPanel bucket={bucket} />
          </QueryBoundary>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel
          title="Busiest endpoints"
          actions={
            <Link to="/endpoints" className="text-2xs text-ink-3 transition-colors hover:text-ink">
              All endpoints
            </Link>
          }
        >
          <QueryBoundary isLoading={endpoints.isLoading} error={endpoints.error} height={220}>
            <BarList
              items={(endpoints.data?.items ?? []).slice(0, 8).map((row) => ({
                label: row.endpoint,
                value: row.requests,
                display: compact(row.requests),
                meta: `${percent(row.error_rate, 1)} err · ${ms(row.p95_response_time)}`,
              }))}
            />
          </QueryBoundary>
        </Panel>

        <Panel
          title="Traffic origin"
          actions={
            <Link to="/geography" className="text-2xs text-ink-3 transition-colors hover:text-ink">
              Map
            </Link>
          }
        >
          <QueryBoundary isLoading={geo.isLoading} error={geo.error} height={220}>
            <BarList
              items={(geo.data?.items ?? []).slice(0, 8).map((row) => ({
                label: `${row.country_name}`,
                value: row.requests,
                display: compact(row.requests),
                meta: percent(row.share, 1),
                color: row.threat_score > 50 ? 'rgb(var(--critical) / 0.18)' : undefined,
              }))}
            />
          </QueryBoundary>
        </Panel>

        <Panel
          title="Open incidents"
          actions={
            <Link to="/incidents" className="text-2xs text-ink-3 transition-colors hover:text-ink">
              All incidents
            </Link>
          }
          dense
          bodyClassName="p-2"
        >
          {(overview.data?.incidents.top ?? []).length === 0 ? (
            <EmptyState title="No open incidents" message="Correlated detections will appear here." />
          ) : (
            <ul className="space-y-1">
              {overview.data!.incidents.top.map((incident) => (
                <li key={incident.incident_id}>
                  <Link
                    to={`/incidents/${incident.incident_id}`}
                    className="group flex items-start gap-2 rounded px-2 py-2 transition-colors hover:bg-raised"
                  >
                    <SeverityBadge severity={incident.severity} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium text-ink">{incident.title}</span>
                      <span className="mt-0.5 block text-2xs text-ink-3">
                        {incident.anomaly_count} detections · started {relativeTime(incident.started_at)}
                      </span>
                    </span>
                    <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </PageTransition>
  )
}

/** p50/p95/p99 on one axis — same unit, so one chart is correct here. */
function LatencyPanel({ bucket }: { bucket: number }) {
  const theme = useChartTheme()
  const { data } = useTimeseries(['p50_response_time', 'p95_response_time', 'p99_response_time'])

  const points = useMemo(() => {
    const p50 = data?.metrics.p50_response_time?.points ?? []
    const p95 = data?.metrics.p95_response_time?.points ?? []
    const p99 = data?.metrics.p99_response_time?.points ?? []
    return p50.map((point, index) => ({
      t: point.t,
      p50: point.v,
      p95: p95[index]?.v ?? null,
      p99: p99[index]?.v ?? null,
    }))
  }, [data])

  return (
    <TimeSeriesChart
      data={points}
      series={[
        { key: 'p50', label: 'p50', color: theme.series[2] },
        { key: 'p95', label: 'p95', color: theme.series[3] },
        { key: 'p99', label: 'p99', color: theme.series[1] },
      ]}
      height={220}
      bucketSeconds={bucket}
      syncId="overview"
      formatValue={(_key, value) => ms(value)}
      formatAxis={(value) => ms(value)}
    />
  )
}

