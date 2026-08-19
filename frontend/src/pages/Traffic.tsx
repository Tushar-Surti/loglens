/**
 * Traffic — volume, composition and how today compares with the learned
 * weekly shape.
 */

import { useMemo, useState } from 'react'

import { BarList } from '@/charts/BarList'
import { Heatmap } from '@/charts/Heatmap'
import { StackedStatusChart } from '@/charts/StackedStatusChart'
import { TimeSeriesChart } from '@/charts/TimeSeriesChart'
import { useChartTheme } from '@/charts/palette'
import { SegmentedControl } from '@/components/Controls'
import { PageHeader } from '@/components/PageHeader'
import { Panel, PageTransition, QueryBoundary } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import {
  useBaseline,
  useOverview,
  useServices,
  useStatusBreakdown,
  useTimeseries,
  useTrafficPatterns,
} from '@/hooks/useApi'
import { bytes, compact, ms, number, percent } from '@/lib/format'

const HEAT_METRICS = [
  { value: 'requests', label: 'Volume' },
  { value: 'error_rate', label: 'Errors' },
  { value: 'p95_response_time', label: 'Latency' },
] as const

export function Traffic() {
  const theme = useChartTheme()
  const [heatMetric, setHeatMetric] = useState<(typeof HEAT_METRICS)[number]['value']>('requests')

  const overview = useOverview()
  const volume = useTimeseries(['requests', 'unique_ips', 'unique_sessions', 'bytes_sent'])
  const shape = useTimeseries(['bot_ratio', 'cache_hit_rate', 'ip_gini', 'path_entropy'])
  const status = useStatusBreakdown()
  const patterns = useTrafficPatterns()
  const services = useServices()
  const baseline = useBaseline('requests')

  const totals = overview.data?.totals
  const bucket = overview.data?.range.bucket_seconds ?? 60

  const volumePoints = useMemo(() => {
    const requests = volume.data?.metrics.requests?.points ?? []
    const sessions = volume.data?.metrics.unique_sessions?.points ?? []
    const ips = volume.data?.metrics.unique_ips?.points ?? []
    return requests.map((point, index) => ({
      t: point.t,
      requests: point.v,
      sessions: sessions[index]?.v ?? null,
      ips: ips[index]?.v ?? null,
    }))
  }, [volume.data])

  const shapePoints = useMemo(() => {
    const bot = shape.data?.metrics.bot_ratio?.points ?? []
    const cache = shape.data?.metrics.cache_hit_rate?.points ?? []
    const gini = shape.data?.metrics.ip_gini?.points ?? []
    const entropy = shape.data?.metrics.path_entropy?.points ?? []
    return bot.map((point, index) => ({
      t: point.t,
      bot_ratio: point.v,
      cache_hit_rate: cache[index]?.v ?? null,
      ip_gini: gini[index]?.v ?? null,
      path_entropy: entropy[index]?.v ?? null,
    }))
  }, [shape.data])

  const methodTotals = useMemo(() => {
    // method_counts is stored per window; the overview already sums them.
    const counts = (patterns.data as any)?.method_counts ?? null
    if (counts) return counts as Record<string, number>
    return null
  }, [patterns.data])

  return (
    <PageTransition>
      <PageHeader
        title="Traffic"
        description="Volume, composition and the shape of demand compared with its learned weekly pattern."
      />

      <StatStrip className="mb-4">
        <StatTile label="Requests" value={compact(totals?.requests ?? 0)} delta={overview.data?.deltas.requests} />
        <StatTile label="Average rate" value={`${number(totals?.rps ?? 0, 1)}/s`} footer={`peak ${number(totals?.peak_rps ?? 0)} rps`} />
        <StatTile label="Data served" value={bytes(totals?.bytes_sent ?? 0)} />
        <StatTile label="Unique IPs" value={compact(totals?.unique_ips ?? 0)} />
        <StatTile label="Bot share" value={percent(totals?.bot_ratio ?? 0, 1)} hint="Requests from identified crawlers and scripted clients" />
        <StatTile label="Cache hit rate" value={percent(totals?.cache_hit_rate ?? 0, 1)} tone={(totals?.cache_hit_rate ?? 0) < 0.4 ? 'warn' : 'good'} />
      </StatStrip>

      <Panel title="Volume, sessions and sources" subtitle="one axis per chart — counts only" className="mb-4">
        <QueryBoundary
          isLoading={volume.isLoading}
          error={volume.error}
          isEmpty={!volumePoints.length}
          onRetry={volume.refetch}
          height={300}
        >
          <TimeSeriesChart
            data={volumePoints}
            series={[
              { key: 'requests', label: 'Requests', color: theme.series[0] },
              { key: 'sessions', label: 'Sessions', color: theme.series[2] },
              { key: 'ips', label: 'Unique IPs', color: theme.series[6] },
            ]}
            height={300}
            bucketSeconds={bucket}
            syncId="traffic"
            formatValue={(_key, value) => compact(value)}
          />
        </QueryBoundary>
      </Panel>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Status composition" subtitle="requests by class">
          <QueryBoundary isLoading={status.isLoading} error={status.error} height={240}>
            <StackedStatusChart
              data={status.data?.points ?? []}
              classes={status.data?.classes ?? []}
              bucketSeconds={bucket}
              height={240}
              syncId="traffic"
            />
          </QueryBoundary>
        </Panel>

        <Panel
          title="Traffic shape indicators"
          subtitle="normalised 0–1 — the inputs behind DDoS and bot detection"
        >
          <QueryBoundary isLoading={shape.isLoading} error={shape.error} height={240}>
            <TimeSeriesChart
              data={shapePoints}
              series={[
                { key: 'ip_gini', label: 'Source concentration', color: theme.series[1] },
                { key: 'path_entropy', label: 'Path entropy', color: theme.series[4] },
                { key: 'bot_ratio', label: 'Bot share', color: theme.series[6] },
                { key: 'cache_hit_rate', label: 'Cache hits', color: theme.series[2] },
              ]}
              height={240}
              bucketSeconds={bucket}
              syncId="traffic"
              yDomain={[0, 1]}
              formatValue={(_key, value) => value.toFixed(3)}
              formatAxis={(value) => value.toFixed(1)}
            />
          </QueryBoundary>
        </Panel>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel
          className="xl:col-span-2"
          title="Weekly traffic pattern"
          subtitle="hour of day × day of week, UTC"
          actions={
            <SegmentedControl
              options={HEAT_METRICS.map((item) => ({ value: item.value, label: item.label }))}
              value={heatMetric}
              onChange={setHeatMetric}
              size="sm"
              ariaLabel="Heatmap metric"
            />
          }
        >
          <QueryBoundary
            isLoading={patterns.isLoading}
            error={patterns.error}
            isEmpty={!patterns.data?.heatmap.length}
            height={240}
            emptyTitle="Not enough history"
            emptyMessage="Run the backfill job to populate historical patterns."
          >
            <Heatmap cells={(patterns.data?.heatmap ?? []) as any} metric={heatMetric} />
          </QueryBoundary>
        </Panel>

        <Panel title="Operating profiles" subtitle="k-means over hourly behaviour">
          <QueryBoundary
            isLoading={patterns.isLoading}
            error={patterns.error}
            isEmpty={!patterns.data?.profiles.length}
            height={240}
            emptyTitle="No profiles yet"
            emptyMessage="Profiles are computed by the training job once enough history exists."
          >
            <ul className="space-y-2.5">
              {(patterns.data?.profiles ?? []).map((profile: any) => (
                <li key={profile.cluster} className="rounded border border-line px-3 py-2">
                  <div className="flex items-baseline justify-between">
                    <span className="text-sm font-medium capitalize text-ink">{profile.name}</span>
                    <span className="tnum text-2xs text-ink-3">{profile.hours}h observed</span>
                  </div>
                  <dl className="mt-1.5 grid grid-cols-3 gap-2 text-2xs">
                    <div>
                      <dt className="text-ink-3">req/h</dt>
                      <dd className="tnum text-ink-2">{compact(profile.avg_requests_per_hour)}</dd>
                    </div>
                    <div>
                      <dt className="text-ink-3">errors</dt>
                      <dd className="tnum text-ink-2">{percent(profile.avg_error_rate, 2)}</dd>
                    </div>
                    <div>
                      <dt className="text-ink-3">p95</dt>
                      <dd className="tnum text-ink-2">{ms(profile.avg_p95_ms)}</dd>
                    </div>
                  </dl>
                  {profile.typical_hours_utc?.length ? (
                    <p className="mt-1.5 text-2xs text-ink-3">
                      typical hours {profile.typical_hours_utc.slice(0, 8).map((h: number) => `${h}:00`).join(', ')}
                      {profile.typical_hours_utc.length > 8 ? '…' : ''}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          </QueryBoundary>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Service throughput" subtitle="per upstream service">
          <QueryBoundary isLoading={services.isLoading} error={services.error} height={200}>
            <BarList
              items={(services.data?.items ?? []).map((row: any) => ({
                label: row.service,
                value: row.requests,
                display: compact(row.requests),
                meta: `${percent(row.error_rate, 2)} err · ${ms(row.p95_response_time)} p95`,
                color:
                  row.status === 'critical'
                    ? 'rgb(var(--critical) / 0.18)'
                    : row.status === 'degraded'
                      ? 'rgb(var(--warn) / 0.18)'
                      : undefined,
              }))}
            />
          </QueryBoundary>
        </Panel>

        <Panel
          title="Seasonal baseline"
          subtitle={baseline.data?.available ? `${baseline.data.day_type} profile, 15-minute buckets` : 'not yet learned'}
        >
          <QueryBoundary
            isLoading={baseline.isLoading}
            error={baseline.error}
            isEmpty={!baseline.data?.available}
            height={200}
            emptyTitle="Baseline not available"
            emptyMessage="The training job builds this from at least a day of history."
          >
            <TimeSeriesChart
              data={(baseline.data?.buckets ?? []).map((entry: any) => ({
                t: new Date(Date.UTC(2026, 0, 1, Math.floor(entry.minute_of_day / 60), entry.minute_of_day % 60)).toISOString(),
                median: entry.median,
                p75: entry.p75,
                p25: entry.p25,
              }))}
              series={[
                { key: 'p75', label: 'p75', color: theme.series[0], strokeDasharray: '3 3' },
                { key: 'median', label: 'Median', color: theme.series[0] },
                { key: 'p25', label: 'p25', color: theme.series[0], strokeDasharray: '3 3' },
              ]}
              height={200}
              bucketSeconds={900}
              formatValue={(_key, value) => compact(value)}
            />
          </QueryBoundary>
        </Panel>
      </div>
    </PageTransition>
  )
}
