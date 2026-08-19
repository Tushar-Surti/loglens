/**
 * Anomalies — the detection feed, with the evidence that produced each score.
 *
 * The point of this page is that a detection is never just a number: every row
 * can be expanded into the detectors that fired, what each measured, and the
 * baseline it was measured against.
 */

import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronRight, Filter } from 'lucide-react'
import { useMemo, useState } from 'react'

import { BarList } from '@/charts/BarList'
import { TimeSeriesChart } from '@/charts/TimeSeriesChart'
import { severityColor, useChartTheme } from '@/charts/palette'
import { GroundTruthBadge, SeverityBadge, SeverityDot } from '@/components/Badges'
import { SegmentedControl } from '@/components/Controls'
import { DefinitionList, Drawer } from '@/components/Drawer'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, Panel, PageTransition, SkeletonRows } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import { useAnomalies, useAnomaly, useAnomalySummary, useUpdateAnomaly } from '@/hooks/useApi'
import { clock, cn, compact, dateTime, ms, number, percent, relativeTime, titleCase } from '@/lib/format'
import type { Anomaly } from '@/lib/types'

const SEVERITY_FILTERS = [
  { value: 'info', label: 'All' },
  { value: 'medium', label: 'Medium+' },
  { value: 'high', label: 'High+' },
  { value: 'critical', label: 'Critical' },
] as const

const ENTITY_FILTERS = [
  { value: '', label: 'Everything' },
  { value: 'global', label: 'Platform' },
  { value: 'endpoint', label: 'Endpoints' },
  { value: 'ip', label: 'Sources' },
  { value: 'country', label: 'Origins' },
] as const

export function Anomalies() {
  const theme = useChartTheme()
  const [severity, setSeverity] = useState<string>('medium')
  const [entityType, setEntityType] = useState<string>('')
  const [selected, setSelected] = useState<string | null>(null)

  const summary = useAnomalySummary()
  const anomalies = useAnomalies({
    severity,
    entity_type: entityType || undefined,
    limit: 120,
  })

  const timeline = summary.data?.timeline ?? []
  const bySeverity = summary.data?.by_severity ?? {}
  const quality = summary.data?.detection_quality

  const grouped = useMemo(() => {
    const items = anomalies.data?.items ?? []
    const buckets = new Map<string, Anomaly[]>()
    for (const item of items) {
      const key = new Date(item.window_start).toISOString().slice(0, 16)
      const list = buckets.get(key) ?? []
      list.push(item)
      buckets.set(key, list)
    }
    return [...buckets.entries()].sort((a, b) => b[0].localeCompare(a[0]))
  }, [anomalies.data])

  return (
    <PageTransition>
      <PageHeader
        title="Anomalies"
        description="Every detection with its score, the detectors that agreed, and the evidence behind them."
        actions={
          <>
            <SegmentedControl
              options={ENTITY_FILTERS.map((item) => ({ value: item.value, label: item.label }))}
              value={entityType}
              onChange={setEntityType}
              ariaLabel="Entity filter"
            />
            <SegmentedControl
              options={SEVERITY_FILTERS.map((item) => ({ value: item.value, label: item.label }))}
              value={severity}
              onChange={setSeverity}
              ariaLabel="Severity filter"
            />
          </>
        }
      />

      <StatStrip className="mb-4 lg:grid-cols-5">
        <StatTile label="Detections" value={compact(anomalies.data?.total ?? 0)} footer="in the selected range" />
        <StatTile label="Critical" value={bySeverity.critical ?? 0} tone={bySeverity.critical ? 'critical' : 'default'} />
        <StatTile label="High" value={bySeverity.high ?? 0} tone={bySeverity.high ? 'warn' : 'default'} />
        <StatTile label="Medium" value={bySeverity.medium ?? 0} />
        <StatTile
          label="Detection F1"
          value={quality ? quality.f1.toFixed(2) : '—'}
          footer={quality ? `precision ${quality.precision.toFixed(2)} · recall ${quality.recall.toFixed(2)}` : 'not evaluated yet'}
          hint="Measured against injected ground truth by the evaluation job"
        />
      </StatStrip>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Detections over time" subtitle="stacked by severity">
          {summary.isLoading ? (
            <SkeletonRows rows={5} />
          ) : summary.error ? (
            <ErrorState error={summary.error} onRetry={summary.refetch} compact />
          ) : !timeline.length ? (
            <EmptyState title="No detections in range" message="The detectors are running; the traffic looks normal." />
          ) : (
            <TimeSeriesChart
              data={timeline}
              series={[
                { key: 'critical', label: 'Critical', color: severityColor('critical', theme) },
                { key: 'high', label: 'High', color: severityColor('high', theme) },
                { key: 'medium', label: 'Medium', color: severityColor('medium', theme) },
                { key: 'low', label: 'Low', color: severityColor('low', theme) },
              ]}
              variant="area"
              height={230}
              bucketSeconds={summary.data?.range.bucket_seconds ?? 60}
              formatValue={(_key, value) => number(value)}
            />
          )}
        </Panel>

        <Panel title="What is being detected" subtitle="by detection type">
          {summary.isLoading ? (
            <SkeletonRows rows={6} />
          ) : (
            <BarList
              items={(summary.data?.by_type ?? []).slice(0, 9).map((row) => ({
                label: row.label,
                value: row.count,
                display: number(row.count),
                meta: `peak ${row.max_score.toFixed(0)}`,
              }))}
              emptyMessage="Nothing detected in this range"
            />
          )}
        </Panel>
      </div>

      {quality ? <DetectionQualityPanel quality={quality} /> : null}

      <Panel
        title="Detection feed"
        subtitle={`${anomalies.data?.items.length ?? 0} shown`}
        dense
        className="mt-4"
        actions={<Filter className="h-3.5 w-3.5 text-ink-3" aria-hidden />}
      >
        {anomalies.isLoading ? (
          <SkeletonRows rows={10} className="p-4" />
        ) : anomalies.error ? (
          <ErrorState error={anomalies.error} onRetry={anomalies.refetch} />
        ) : !grouped.length ? (
          <EmptyState
            title="No anomalies matched"
            message="Lower the severity filter, or use Simulate to inject an incident."
          />
        ) : (
          <div className="divide-y divide-line">
            {grouped.map(([minute, items]) => (
              <div key={minute}>
                <div className="sticky top-0 z-10 flex items-center gap-2 bg-surface/95 px-4 py-1.5 backdrop-blur-sm">
                  <span className="tnum font-mono text-2xs text-ink-3">{clock(items[0].window_start, false)}</span>
                  <span className="text-2xs text-ink-3">{relativeTime(items[0].window_start)}</span>
                  <span className="ml-auto text-2xs text-ink-3">{items.length} detections</span>
                </div>
                <ul>
                  {items.map((anomaly) => (
                    <li key={anomaly.anomaly_id}>
                      <button
                        type="button"
                        onClick={() => setSelected(anomaly.anomaly_id)}
                        className="group flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors hover:bg-raised/60"
                      >
                        <SeverityDot severity={anomaly.severity} />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium text-ink">{anomaly.headline}</span>
                            <GroundTruthBadge label={anomaly.ground_truth} />
                          </div>
                          <p className="mt-0.5 line-clamp-2 text-xs text-ink-3">{anomaly.reason}</p>
                          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                            <span className="chip">{anomaly.type_label}</span>
                            <span className="chip font-mono">{anomaly.entity}</span>
                            {anomaly.detectors.slice(0, 3).map((detector) => (
                              <span key={detector} className="chip text-ink-3">
                                {titleCase(detector)}
                              </span>
                            ))}
                            {anomaly.agreement > 1 ? (
                              <span className="chip border-accent/40 text-accent">
                                {anomaly.agreement} detectors agree
                              </span>
                            ) : null}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-3">
                          <SeverityBadge severity={anomaly.severity} score={anomaly.score} size="sm" />
                          <ChevronRight className="h-4 w-4 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100" />
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <AnomalyDrawer anomalyId={selected} onClose={() => setSelected(null)} />
    </PageTransition>
  )
}

function DetectionQualityPanel({ quality }: { quality: any }) {
  const scenarios = Object.entries(quality.per_scenario ?? {}) as [string, any][]
  return (
    <Panel
      title="Detection quality"
      subtitle={`measured against injected ground truth · ${quality.minutes_evaluated} minutes evaluated`}
    >
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: 'Precision', value: quality.precision, hint: 'of the windows we flagged, how many were real' },
            { label: 'Recall', value: quality.recall, hint: 'of the real incidents, how many we caught' },
            { label: 'F1', value: quality.f1, hint: 'harmonic mean of the two' },
            { label: 'False positive rate', value: quality.false_positive_rate, hint: 'quiet windows we flagged anyway' },
          ].map((metric) => (
            <div key={metric.label} className="rounded border border-line px-3 py-2" title={metric.hint}>
              <p className="label-caps">{metric.label}</p>
              <p className="tnum mt-0.5 text-xl font-semibold text-ink">{(metric.value ?? 0).toFixed(3)}</p>
            </div>
          ))}
          <div className="col-span-2 rounded border border-line px-3 py-2">
            <p className="label-caps">Confusion matrix</p>
            <div className="mt-1 grid grid-cols-4 gap-2 text-center text-2xs">
              {(['tp', 'fp', 'fn', 'tn'] as const).map((key) => (
                <div key={key}>
                  <p className="tnum text-sm font-semibold text-ink">{quality.confusion?.[key] ?? 0}</p>
                  <p className="uppercase text-ink-3">{key}</p>
                </div>
              ))}
            </div>
            {quality.mean_detection_latency_minutes !== null ? (
              <p className="mt-2 text-2xs text-ink-3">
                mean detection latency {quality.mean_detection_latency_minutes} min
              </p>
            ) : null}
          </div>
        </div>

        <div>
          <p className="label-caps mb-2">Recall per injected scenario</p>
          {scenarios.length === 0 ? (
            <p className="text-xs text-ink-3">No labelled incidents in the evaluation window.</p>
          ) : (
            <ul className="space-y-1.5">
              {scenarios.map(([name, stats]) => (
                <li key={name} className="flex items-center gap-3">
                  <span className="w-40 shrink-0 truncate text-xs text-ink-2">{titleCase(name)}</span>
                  <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-line">
                    <span
                      className={cn(
                        'block h-full rounded-full',
                        stats.recall > 0.8 ? 'bg-good' : stats.recall > 0.5 ? 'bg-warn' : 'bg-critical',
                      )}
                      style={{ width: `${Math.max(2, stats.recall * 100)}%` }}
                    />
                  </span>
                  <span className="tnum w-12 shrink-0 text-right text-2xs text-ink-2">
                    {percent(stats.recall, 0)}
                  </span>
                  <span className="tnum hidden w-20 shrink-0 text-right text-2xs text-ink-3 sm:block">
                    {stats.true_positives}/{stats.true_positives + stats.false_negatives}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  )
}

function AnomalyDrawer({ anomalyId, onClose }: { anomalyId: string | null; onClose: () => void }) {
  const theme = useChartTheme()
  const detail = useAnomaly(anomalyId)
  const update = useUpdateAnomaly()
  const anomaly = detail.data?.anomaly

  const context = useMemo(() => {
    const rows = detail.data?.context ?? []
    return rows.map((row: any) => ({
      t: row.window_start,
      requests: row.requests ?? 0,
      error_rate: row.error_rate ?? row.error_ratio ?? 0,
      p95: row.p95_response_time ?? 0,
    }))
  }, [detail.data])

  return (
    <Drawer
      open={Boolean(anomalyId)}
      onClose={onClose}
      title={anomaly?.headline ?? 'Detection'}
      subtitle={anomaly ? `${anomaly.type_label} · ${dateTime(anomaly.window_start)}` : undefined}
      width="max-w-3xl"
      actions={
        anomaly && anomaly.status === 'open' ? (
          <button
            type="button"
            className="btn btn-sm"
            disabled={update.isPending}
            onClick={() => update.mutate({ id: anomaly.anomaly_id, status: 'acknowledged' })}
          >
            <Check className="h-3.5 w-3.5" aria-hidden />
            Acknowledge
          </button>
        ) : null
      }
    >
      {detail.isLoading ? (
        <SkeletonRows rows={10} className="p-4" />
      ) : !anomaly ? (
        <EmptyState title="Detection not found" />
      ) : (
        <div className="space-y-5 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={anomaly.severity} score={anomaly.score} />
            <span className="chip">confidence {(anomaly.confidence * 100).toFixed(0)}%</span>
            <span className="chip font-mono">{anomaly.entity}</span>
            <span className="chip">{anomaly.status}</span>
            <GroundTruthBadge label={anomaly.ground_truth} />
          </div>

          <p className="rounded border border-line bg-raised/40 px-3 py-2.5 text-sm leading-relaxed text-ink-2">
            {anomaly.reason}
          </p>

          {context.length > 1 ? (
            <div>
              <p className="label-caps mb-2">Surrounding windows</p>
              <TimeSeriesChart
                data={context}
                series={[{ key: 'requests', label: 'Requests', color: theme.series[0] }]}
                variant="area"
                height={150}
                anomalies={[{ start: anomaly.window_start, end: anomaly.window_end, severity: anomaly.severity }]}
                showLegend={false}
                formatValue={(_key, value) => compact(value)}
              />
            </div>
          ) : null}

          <div>
            <p className="label-caps mb-2">How the score was reached</p>
            <ul className="space-y-2">
              {(anomaly.contributing ?? []).map((contribution) => (
                <li key={contribution.detector} className="rounded border border-line">
                  <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-1.5">
                    <span className="text-xs font-medium text-ink">{titleCase(contribution.detector)}</span>
                    <span className="flex items-center gap-2">
                      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-line">
                        <span
                          className={cn('block h-full rounded-full', contribution.triggered ? 'bg-critical' : 'bg-accent')}
                          style={{ width: `${Math.max(2, contribution.score * 100)}%` }}
                        />
                      </span>
                      <span className="tnum w-9 text-right text-2xs text-ink-2">
                        {contribution.score.toFixed(2)}
                      </span>
                    </span>
                  </div>
                  <p className="px-3 py-2 text-xs leading-relaxed text-ink-2">{contribution.reason}</p>
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1 border-t border-line px-3 py-2 text-2xs sm:grid-cols-3">
                    {Object.entries(contribution.evidence ?? {})
                      .filter(([, value]) => typeof value !== 'object')
                      .slice(0, 9)
                      .map(([key, value]) => (
                        <div key={key} className="flex items-baseline justify-between gap-2">
                          <dt className="truncate text-ink-3">{key.replace(/_/g, ' ')}</dt>
                          <dd className="tnum shrink-0 font-mono text-ink-2">{String(value)}</dd>
                        </div>
                      ))}
                  </dl>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="label-caps mb-2">Window metrics</p>
            <DefinitionList
              columns={3}
              items={Object.entries(anomaly.metrics ?? {}).map(([key, value]) => ({
                label: key.replace(/_/g, ' '),
                value:
                  typeof value === 'number'
                    ? key.includes('rate') || key.includes('ratio')
                      ? percent(value, 2)
                      : key.includes('response_time')
                        ? ms(value)
                        : number(value, 2)
                    : String(value ?? '—'),
              }))}
            />
          </div>

          {detail.data?.sample_logs?.length ? (
            <div>
              <p className="label-caps mb-2">Sample requests from {anomaly.entity}</p>
              <ul className="divide-y divide-line rounded border border-line text-2xs">
                {detail.data.sample_logs.slice(0, 10).map((event) => (
                  <li key={event.event_id} className="flex items-center justify-between gap-3 px-3 py-1.5">
                    <span className="truncate font-mono text-ink-2">
                      {event.method} {event.path}
                    </span>
                    <span className="tnum shrink-0 text-ink-3">
                      {event.status} · {ms(event.response_time_ms)} · {clock(event.timestamp, true)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </Drawer>
  )
}
