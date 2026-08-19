/** One incident: what happened, when, to what, and what the platform did about it. */

import { ArrowLeft, Check, Siren } from 'lucide-react'
import { useMemo } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { TimeSeriesChart } from '@/charts/TimeSeriesChart'
import { useChartTheme } from '@/charts/palette'
import { GroundTruthBadge, SeverityBadge, SeverityDot } from '@/components/Badges'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, Panel, PageTransition, SkeletonRows } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import { useIncident, useUpdateIncident } from '@/hooks/useApi'
import { clock, cn, compact, dateTime, duration, ms, percent, relativeTime, titleCase } from '@/lib/format'

export function IncidentDetail() {
  const { incidentId } = useParams()
  const navigate = useNavigate()
  const theme = useChartTheme()
  const detail = useIncident(incidentId)
  const update = useUpdateIncident()

  const incident = detail.data?.incident
  const metrics = useMemo(
    () =>
      (detail.data?.metrics ?? []).map((row: any) => ({
        t: row.window_start,
        requests: row.requests,
        error_rate: row.error_rate,
        p95: row.p95_response_time,
      })),
    [detail.data],
  )

  if (detail.isLoading) return <SkeletonRows rows={12} className="p-4" />
  if (detail.error) return <ErrorState error={detail.error} onRetry={detail.refetch} />
  if (!incident) return <EmptyState title="Incident not found" />

  const band = [{ start: incident.started_at, end: incident.last_seen_at, severity: incident.severity }]

  return (
    <PageTransition>
      <button type="button" onClick={() => navigate('/incidents')} className="btn btn-ghost btn-sm mb-3">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        All incidents
      </button>

      <PageHeader
        title={incident.title}
        description={incident.summary}
        meta={
          <>
            <SeverityBadge severity={incident.severity} score={incident.peak_score} />
            <span className="chip">{incident.family_label ?? titleCase(incident.family ?? '')}</span>
            <span className="chip font-mono">{incident.entity}</span>
            <span className="chip">{incident.status}</span>
            <GroundTruthBadge label={incident.ground_truth} />
          </>
        }
        actions={
          incident.status !== 'resolved' ? (
            <>
              {incident.status === 'open' ? (
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={update.isPending}
                  onClick={() => update.mutate({ id: incident.incident_id, status: 'acknowledged' })}
                >
                  <Check className="h-3.5 w-3.5" aria-hidden />
                  Acknowledge
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-sm btn-accent"
                disabled={update.isPending}
                onClick={() => update.mutate({ id: incident.incident_id, status: 'resolved', resolution: 'manual' })}
              >
                Resolve
              </button>
            </>
          ) : null
        }
      />

      <StatStrip className="mb-4 lg:grid-cols-5">
        <StatTile label="Detections" value={incident.anomaly_count} footer={`${incident.detectors?.length ?? 0} detectors involved`} />
        <StatTile label="Duration" value={duration((incident.impact?.duration_minutes ?? 0) * 60)} footer={`from ${clock(incident.started_at, true)}`} />
        <StatTile label="Requests affected" value={compact(incident.impact?.requests ?? 0)} />
        <StatTile
          label="Peak error rate"
          value={percent(incident.impact?.peak_error_rate ?? 0, 1)}
          tone={(incident.impact?.peak_error_rate ?? 0) > 0.1 ? 'critical' : 'default'}
        />
        <StatTile label="Peak p95" value={ms(incident.impact?.peak_p95_ms ?? 0)} />
      </StatStrip>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Panel className="xl:col-span-2" title="Platform metrics during the incident">
          {metrics.length < 2 ? (
            <EmptyState title="Not enough context" message="The incident is shorter than one metric window." />
          ) : (
            <>
              <TimeSeriesChart
                data={metrics}
                series={[{ key: 'requests', label: 'Requests', color: theme.series[0] }]}
                variant="area"
                height={170}
                anomalies={band}
                showLegend={false}
                syncId="incident"
                formatValue={(_key, value) => compact(value)}
              />
              <TimeSeriesChart
                data={metrics}
                series={[{ key: 'p95', label: 'p95 latency', color: theme.series[3] }]}
                height={140}
                anomalies={band}
                showLegend={false}
                syncId="incident"
                formatValue={(_key, value) => ms(value)}
                formatAxis={(value) => ms(value)}
              />
            </>
          )}
        </Panel>

        <Panel title="Timeline" dense bodyClassName="max-h-[420px] overflow-y-auto p-4">
          <ol className="relative space-y-3 border-l border-line pl-4">
            {(incident.timeline ?? []).map((entry, index) => (
              <li key={`${entry.at}-${index}`} className="relative">
                <span className="absolute -left-[21px] top-1.5" aria-hidden>
                  <SeverityDot severity={entry.severity} />
                </span>
                <p className="text-xs font-medium text-ink">{entry.reason}</p>
                <p className="mt-0.5 flex flex-wrap items-center gap-2 text-2xs text-ink-3">
                  <span className="tnum font-mono">{clock(entry.at, true)}</span>
                  <span>·</span>
                  <span>{titleCase(entry.type)}</span>
                  <span>·</span>
                  <span className="tnum">score {entry.score.toFixed(0)}</span>
                </p>
              </li>
            ))}
          </ol>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Contributing detections" subtitle={`${detail.data?.anomalies.length ?? 0} anomalies`} dense>
          <ul className="max-h-[420px] divide-y divide-line overflow-y-auto">
            {(detail.data?.anomalies ?? []).map((anomaly) => (
              <li key={anomaly.anomaly_id} className="px-4 py-2.5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-ink">{anomaly.headline}</p>
                    <p className="mt-0.5 text-2xs text-ink-3">
                      <span className="font-mono">{anomaly.entity}</span> · {clock(anomaly.window_start, true)} ·{' '}
                      {titleCase(anomaly.detector)}
                    </p>
                  </div>
                  <SeverityBadge severity={anomaly.severity} score={anomaly.score} size="sm" />
                </div>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-4">
          <Panel title="Alerts fired" dense>
            {(detail.data?.alerts ?? []).length === 0 ? (
              <EmptyState title="No alerts fired" message="No enabled rule matched this incident." />
            ) : (
              <ul className="divide-y divide-line">
                {detail.data!.alerts.map((alert) => (
                  <li key={alert.alert_id} className="flex items-start gap-2.5 px-4 py-2.5">
                    <Siren className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium text-ink">{alert.rule_name}</p>
                      <p className="mt-0.5 text-2xs text-ink-3">
                        {relativeTime(alert.created_at)} · {alert.channels.join(', ')}
                        {alert.webhook_status ? ` · webhook ${alert.webhook_status}` : ''}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Affected entities">
            {(incident.entities ?? []).length === 0 ? (
              <p className="text-xs text-ink-3">Platform-wide — no single entity.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {incident.entities.slice(0, 40).map((entity) => (
                  <span key={entity} className="chip font-mono">
                    {entity}
                  </span>
                ))}
              </div>
            )}
            <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
              <div>
                <dt className="label-caps">Started</dt>
                <dd className="mt-0.5 text-ink-2">{dateTime(incident.started_at)}</dd>
              </div>
              <div>
                <dt className="label-caps">Last seen</dt>
                <dd className="mt-0.5 text-ink-2">{dateTime(incident.last_seen_at)}</dd>
              </div>
              <div>
                <dt className="label-caps">Detectors</dt>
                <dd className="mt-0.5 text-ink-2">{(incident.detectors ?? []).map(titleCase).join(', ') || '—'}</dd>
              </div>
              <div>
                <dt className="label-caps">Detection types</dt>
                <dd className="mt-0.5 text-ink-2">{(incident.types ?? []).map(titleCase).join(', ') || '—'}</dd>
              </div>
            </dl>
          </Panel>
        </div>
      </div>
    </PageTransition>
  )
}
