/** Incidents — correlated groups of detections, one row per real event. */

import { ArrowUpRight } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { GroundTruthBadge, SeverityBadge } from '@/components/Badges'
import { SegmentedControl } from '@/components/Controls'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, Panel, PageTransition, SkeletonRows } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import { useIncidents } from '@/hooks/useApi'
import { cn, compact, dateTime, duration, ms, percent, relativeTime, titleCase } from '@/lib/format'

const STATUS_FILTERS = [
  { value: '', label: 'All' },
  { value: 'open', label: 'Open' },
  { value: 'acknowledged', label: 'Acknowledged' },
  { value: 'resolved', label: 'Resolved' },
] as const

const FAMILY_TONE: Record<string, string> = {
  security: 'border-critical/40 text-critical',
  availability: 'border-serious/40 text-serious',
  performance: 'border-warn/40 text-warn',
  traffic: 'border-accent/40 text-accent',
  behaviour: 'border-line text-ink-2',
}

export function Incidents() {
  const [status, setStatus] = useState<string>('')
  const incidents = useIncidents({ status: status || undefined, limit: 100 })

  const items = incidents.data?.items ?? []
  const counts = incidents.data?.by_status ?? {}

  return (
    <PageTransition>
      <PageHeader
        title="Incidents"
        description="Detections correlated into one record per real event — a botnet is one incident, not eighty."
        actions={
          <SegmentedControl
            options={STATUS_FILTERS.map((item) => ({ value: item.value, label: item.label }))}
            value={status}
            onChange={setStatus}
            ariaLabel="Incident status"
          />
        }
      />

      <StatStrip className="mb-4 lg:grid-cols-4">
        <StatTile label="Open" value={counts.open ?? 0} tone={(counts.open ?? 0) > 0 ? 'critical' : 'good'} />
        <StatTile label="Acknowledged" value={counts.acknowledged ?? 0} />
        <StatTile label="Resolved" value={counts.resolved ?? 0} tone="good" />
        <StatTile label="Total in range" value={incidents.data?.total ?? 0} />
      </StatStrip>

      <Panel title="Incident log" subtitle={`${items.length} shown`} dense>
        {incidents.isLoading ? (
          <SkeletonRows rows={8} className="p-4" />
        ) : incidents.error ? (
          <ErrorState error={incidents.error} onRetry={incidents.refetch} />
        ) : !items.length ? (
          <EmptyState
            title="No incidents"
            message="Correlated detections appear here. Use Simulate to inject one end-to-end."
          />
        ) : (
          <ul className="divide-y divide-line">
            {items.map((incident) => (
              <li key={incident.incident_id}>
                <Link
                  to={`/incidents/${incident.incident_id}`}
                  className="group flex flex-col gap-2 px-4 py-3 transition-colors hover:bg-raised/60 sm:flex-row sm:items-center"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={incident.severity} score={incident.peak_score} size="sm" />
                      <span
                        className={cn(
                          'chip',
                          FAMILY_TONE[incident.family] ?? 'border-line text-ink-2',
                        )}
                      >
                        {incident.family_label ?? titleCase(incident.family ?? '')}
                      </span>
                      <span className="text-sm font-medium text-ink">{incident.title}</span>
                      <GroundTruthBadge label={incident.ground_truth} />
                    </div>
                    <p className="mt-1 line-clamp-1 text-xs text-ink-3">{incident.summary}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-ink-3">
                      <span>{incident.anomaly_count} detections</span>
                      <span>{duration((incident.impact?.duration_minutes ?? 0) * 60)} long</span>
                      {incident.impact?.requests ? <span>{compact(incident.impact.requests)} requests affected</span> : null}
                      {incident.impact?.peak_error_rate ? (
                        <span>peak {percent(incident.impact.peak_error_rate, 1)} errors</span>
                      ) : null}
                      {incident.impact?.peak_p95_ms ? <span>peak p95 {ms(incident.impact.peak_p95_ms)}</span> : null}
                      <span>{incident.detectors?.length ?? 0} detectors</span>
                    </div>
                  </div>

                  <div className="flex shrink-0 items-center gap-4 sm:flex-col sm:items-end sm:gap-1">
                    <span
                      className={cn(
                        'chip',
                        incident.status === 'open'
                          ? 'border-critical/40 text-critical'
                          : incident.status === 'acknowledged'
                            ? 'border-warn/40 text-warn'
                            : 'border-good/40 text-good',
                      )}
                    >
                      {incident.status}
                    </span>
                    <span className="text-2xs text-ink-3" title={dateTime(incident.started_at)}>
                      started {relativeTime(incident.started_at)}
                    </span>
                    <ArrowUpRight className="hidden h-3.5 w-3.5 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100 sm:block" />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </PageTransition>
  )
}
