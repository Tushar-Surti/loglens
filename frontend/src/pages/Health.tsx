/**
 * System Health — the platform monitoring itself.
 *
 * Ingest lag, Kafka consumer lag, per-query Spark progress and MongoDB
 * footprint. If the dashboard looks quiet, this page says whether that is good
 * news or a broken pipeline.
 */

import { Activity, Database, GitBranch, Server, Zap } from 'lucide-react'
import { useMemo } from 'react'

import { TimeSeriesChart } from '@/charts/TimeSeriesChart'
import { useChartTheme } from '@/charts/palette'
import { HealthPill } from '@/components/Badges'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, Panel, PageTransition, QueryBoundary } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import { useModels, usePipeline, useSystemHealth, useSystemStats } from '@/hooks/useApi'
import { bytes, cn, compact, dateTime, duration, ms, number, percent, relativeTime, titleCase } from '@/lib/format'

const COMPONENT_ICONS: Record<string, JSX.Element> = {
  generator: <Zap className="h-4 w-4" />,
  spark: <GitBranch className="h-4 w-4" />,
  worker: <Server className="h-4 w-4" />,
  api: <Activity className="h-4 w-4" />,
}

export function Health() {
  const theme = useChartTheme()
  const health = useSystemHealth()
  const pipeline = usePipeline()
  const stats = useSystemStats()
  const models = useModels()

  const details = health.data?.details ?? {}
  const kafka = details.kafka ?? {}
  const mongo = details.mongodb ?? {}
  const throughput = details.throughput ?? { events_per_second: 0 }
  const lag = health.data?.pipeline_lag_seconds

  const queries = Object.entries(pipeline.data?.spark_queries ?? {})
  const generator = pipeline.data?.generator ?? {}
  const generatorStats = generator.stats ?? {}

  const batchSeries = useMemo(
    () => (pipeline.data?.throughput ?? []).map((row) => ({ t: row.t, rows: row.rows, duration_ms: row.duration_ms })),
    [pipeline.data],
  )

  return (
    <PageTransition>
      <PageHeader
        title="System Health"
        description="Ingest pipeline, stream processing, storage and model status."
        meta={<HealthPill status={health.data?.status ?? 'unknown'} />}
      />

      <StatStrip className="mb-4">
        <StatTile
          label="Ingest rate"
          value={`${number(throughput.events_per_second ?? 0, 1)}/s`}
          footer="events entering the pipeline"
        />
        <StatTile
          label="Pipeline lag"
          value={lag !== null && lag !== undefined ? duration(lag) : '—'}
          tone={(lag ?? 0) > 300 ? 'critical' : (lag ?? 0) > 150 ? 'warn' : 'good'}
          footer="age of the newest metric window"
        />
        <StatTile
          label="Kafka lag"
          value={compact((kafka as any).total_lag ?? 0)}
          tone={((kafka as any).total_lag ?? 0) > 50_000 ? 'warn' : 'good'}
          footer={(kafka as any).status ?? 'unknown'}
        />
        <StatTile
          label="Ingest latency"
          value={ms((throughput as any).avg_ingest_latency_ms ?? 0)}
          footer="event time → processing time"
        />
        <StatTile label="Storage" value={bytes(((mongo as any).storage_mb ?? 0) * 1_048_576)} footer="MongoDB on disk" />
        <StatTile
          label="Documents"
          value={compact(Object.values(stats.data?.collections ?? {}).reduce((sum, value) => sum + value, 0))}
          footer="across all collections"
        />
      </StatStrip>

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-4">
        {Object.entries(health.data?.components ?? {}).map(([name, component]) => (
          <div
            key={name}
            className={cn(
              'panel px-4 py-3',
              component.status === 'healthy' ? '' : 'border-warn/40',
              component.status === 'critical' && 'border-critical/50',
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-sm font-medium text-ink">
                <span className="text-ink-3">{COMPONENT_ICONS[name] ?? <Server className="h-4 w-4" />}</span>
                {titleCase(name)}
              </span>
              <HealthPill status={component.status} />
            </div>
            <p className="mt-2 text-2xs text-ink-3">
              {component.last_seen ? `last heartbeat ${relativeTime(component.last_seen)}` : 'never seen'}
            </p>
            {name === 'generator' && generatorStats.eps ? (
              <p className="tnum mt-1 text-2xs text-ink-3">
                {number(generatorStats.eps, 0)} eps · {compact(generatorStats.produced ?? 0)} produced
              </p>
            ) : null}
            {name === 'spark' && component.details?.queries ? (
              <p className="tnum mt-1 text-2xs text-ink-3">
                {Array.isArray(component.details.queries) ? component.details.queries.length : component.details.queries}{' '}
                streaming queries
              </p>
            ) : null}
          </div>
        ))}
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Spark streaming queries" subtitle="one row per stateful query" dense>
          <QueryBoundary
            isLoading={pipeline.isLoading}
            error={pipeline.error}
            isEmpty={!queries.length}
            height={280}
            emptyTitle="No Spark telemetry"
            emptyMessage="The streaming application publishes progress every 15 seconds."
          >
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line">
                    <th className="th">Query</th>
                    <th className="th text-right">Batch</th>
                    <th className="th text-right">Input rows/s</th>
                    <th className="th text-right">Processed rows/s</th>
                    <th className="th text-right">Batch time</th>
                    <th className="th text-right">State rows</th>
                    <th className="th text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {queries.map(([name, query]) => (
                    <tr key={name} className="border-b border-line/70">
                      <td className="cell font-mono text-xs text-ink">{name}</td>
                      <td className="cell tnum text-right text-xs">{query.batch_id ?? '—'}</td>
                      <td className="cell tnum text-right text-xs">{number(query.input_rows_per_second ?? 0, 1)}</td>
                      <td className="cell tnum text-right text-xs">{number(query.processed_rows_per_second ?? 0, 1)}</td>
                      <td className="cell tnum text-right text-xs">{ms(query.batch_duration_ms ?? 0)}</td>
                      <td className="cell tnum text-right text-xs">{compact(query.state_rows ?? 0)}</td>
                      <td className="cell text-right">
                        <HealthPill status={query.active ? 'healthy' : 'critical'} label={query.active ? 'running' : 'stopped'} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </QueryBoundary>
        </Panel>

        <Panel title="Kafka topics" subtitle="consumer lag by topic">
          <QueryBoundary isLoading={health.isLoading} error={health.error} height={280}>
            {Object.keys((kafka as any).topics ?? {}).length === 0 ? (
              <EmptyState title="Kafka metrics unavailable" message={(kafka as any).error ?? 'The broker did not respond.'} />
            ) : (
              <ul className="space-y-3">
                {Object.entries((kafka as any).topics ?? {}).map(([topic, info]: [string, any]) => (
                  <li key={topic}>
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-mono text-xs text-ink">{topic}</span>
                      <span className="tnum shrink-0 text-2xs text-ink-3">{info.partitions} partitions</span>
                    </div>
                    <div className="mt-1 flex items-center gap-2">
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
                        <span
                          className={cn('block h-full rounded-full', info.lag > 20_000 ? 'bg-warn' : 'bg-good')}
                          style={{
                            width: `${Math.min(100, (info.lag / Math.max(info.log_end_offset || 1, 1)) * 100 + 2)}%`,
                          }}
                        />
                      </span>
                      <span className="tnum w-16 text-right text-2xs text-ink-2">{compact(info.lag)} lag</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Panel>
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Micro-batch throughput" subtitle="rows written per minute across all sinks">
          <QueryBoundary
            isLoading={pipeline.isLoading}
            error={pipeline.error}
            isEmpty={!batchSeries.length}
            height={200}
          >
            <TimeSeriesChart
              data={batchSeries}
              series={[{ key: 'rows', label: 'Rows written', color: theme.series[0] }]}
              variant="area"
              height={200}
              bucketSeconds={60}
              showLegend={false}
              formatValue={(_key, value) => compact(value)}
            />
          </QueryBoundary>
        </Panel>

        <Panel title="Storage by collection">
          <QueryBoundary isLoading={stats.isLoading} error={stats.error} height={200}>
            <ul className="space-y-1.5">
              {Object.entries(stats.data?.collections ?? {})
                .sort((a, b) => b[1] - a[1])
                .map(([name, count]) => {
                  const max = Math.max(...Object.values(stats.data?.collections ?? { x: 1 }), 1)
                  return (
                    <li key={name} className="flex items-center gap-3">
                      <span className="w-40 shrink-0 truncate font-mono text-2xs text-ink-2">{name}</span>
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
                        <span className="block h-full rounded-full bg-accent/70" style={{ width: `${(count / max) * 100}%` }} />
                      </span>
                      <span className="tnum w-16 shrink-0 text-right text-2xs text-ink-2">{compact(count)}</span>
                    </li>
                  )
                })}
            </ul>
            <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-3 text-xs">
              <div>
                <dt className="label-caps">Data</dt>
                <dd className="tnum mt-0.5 text-ink-2">{bytes((stats.data?.data_mb ?? 0) * 1_048_576)}</dd>
              </div>
              <div>
                <dt className="label-caps">Indexes</dt>
                <dd className="tnum mt-0.5 text-ink-2">{bytes((stats.data?.index_mb ?? 0) * 1_048_576)}</dd>
              </div>
              <div>
                <dt className="label-caps">On disk</dt>
                <dd className="tnum mt-0.5 text-ink-2">{bytes((stats.data?.storage_mb ?? 0) * 1_048_576)}</dd>
              </div>
            </dl>
          </QueryBoundary>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Panel title="Models" subtitle="unsupervised detectors and their training runs" dense>
          <QueryBoundary isLoading={models.isLoading} error={models.error} height={220}>
            {(models.data?.models ?? []).length === 0 ? (
              <EmptyState
                title="No models trained yet"
                message="The worker trains on a schedule; the statistical detectors run regardless."
                icon={<Database className="h-5 w-5" />}
              />
            ) : (
              <ul className="divide-y divide-line">
                {(models.data?.models ?? []).slice(0, 6).map((model: any, index: number) => (
                  <li key={`${model.name}-${index}`} className="px-4 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-ink">{model.name}</span>
                      <span className="text-2xs text-ink-3">{relativeTime(model.trained_at)}</span>
                    </div>
                    <p className="mt-1 text-2xs text-ink-3">
                      {model.metadata?.rows ? `${compact(model.metadata.rows)} training rows · ` : ''}
                      {model.metadata?.trees ? `${model.metadata.trees} trees · ` : ''}
                      {model.features?.length ?? 0} features
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Panel>

        <Panel title="Pipeline configuration" subtitle="effective runtime settings">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
            {Object.entries(pipeline.data?.config ?? {}).map(([key, value]) => (
              <div key={key} className="min-w-0">
                <dt className="label-caps">{key.replace(/_/g, ' ')}</dt>
                <dd className="mt-0.5 truncate font-mono text-2xs text-ink-2">
                  {typeof value === 'object' ? Object.values(value as object).join(', ') : String(value)}
                </dd>
              </div>
            ))}
            {pipeline.data?.backfill ? (
              <div className="col-span-2">
                <dt className="label-caps">Historical backfill</dt>
                <dd className="mt-0.5 text-2xs text-ink-2">
                  {compact(pipeline.data.backfill.events ?? 0)} events over {pipeline.data.backfill.hours}h ·{' '}
                  {compact(pipeline.data.backfill.windows ?? 0)} windows · completed{' '}
                  {relativeTime(pipeline.data.backfill.completed_at ?? null)}
                </dd>
              </div>
            ) : null}
          </dl>
        </Panel>
      </div>
    </PageTransition>
  )
}
