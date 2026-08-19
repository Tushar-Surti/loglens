/** Alerts — what actually paged, and whether anyone has acknowledged it. */

import { Bell, BellOff, Check } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { SeverityBadge } from '@/components/Badges'
import { SegmentedControl } from '@/components/Controls'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, Panel, PageTransition, SkeletonRows } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import { useAlertRules, useAlerts, useUpdateAlert } from '@/hooks/useApi'
import { cn, dateTime, duration, relativeTime, titleCase } from '@/lib/format'

const SEVERITY_FILTERS = [
  { value: '', label: 'All' },
  { value: 'medium', label: 'Medium+' },
  { value: 'high', label: 'High+' },
  { value: 'critical', label: 'Critical' },
] as const

export function Alerts() {
  const [severity, setSeverity] = useState('')
  const alerts = useAlerts({ severity: severity || undefined, limit: 120 })
  const rules = useAlertRules()
  const update = useUpdateAlert()

  const items = alerts.data?.items ?? []
  const unacknowledged = items.filter((alert) => !alert.acknowledged).length

  return (
    <PageTransition>
      <PageHeader
        title="Alerts"
        description="Rule matches that crossed the notification threshold, after de-duplication and cooldown."
        actions={
          <SegmentedControl
            options={SEVERITY_FILTERS.map((item) => ({ value: item.value, label: item.label }))}
            value={severity}
            onChange={setSeverity}
            ariaLabel="Alert severity"
          />
        }
      />

      <StatStrip className="mb-4 lg:grid-cols-4">
        <StatTile label="Alerts" value={alerts.data?.total ?? 0} footer="in the selected range" />
        <StatTile label="Unacknowledged" value={unacknowledged} tone={unacknowledged ? 'critical' : 'good'} />
        <StatTile label="Rules enabled" value={(rules.data?.items ?? []).filter((rule) => rule.enabled).length} />
        <StatTile label="Rules total" value={rules.data?.items.length ?? 0} />
      </StatStrip>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_340px]">
        <Panel title="Alert log" subtitle={`${items.length} shown`} dense>
          {alerts.isLoading ? (
            <SkeletonRows rows={8} className="p-4" />
          ) : alerts.error ? (
            <ErrorState error={alerts.error} onRetry={alerts.refetch} />
          ) : !items.length ? (
            <EmptyState
              title="No alerts fired"
              message="Either nothing crossed a rule, or the cooldown suppressed repeats."
              icon={<BellOff className="h-5 w-5" />}
            />
          ) : (
            <ul className="divide-y divide-line">
              {items.map((alert) => (
                <li key={alert.alert_id} className="flex items-start gap-3 px-4 py-3">
                  <Bell
                    className={cn('mt-0.5 h-4 w-4 shrink-0', alert.acknowledged ? 'text-ink-3' : 'text-warn')}
                    aria-hidden
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={alert.severity} size="sm" />
                      <span className="text-sm font-medium text-ink">{alert.rule_name}</span>
                      {alert.acknowledged ? <span className="chip border-good/40 text-good">acknowledged</span> : null}
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-ink-3">{alert.message || alert.title}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-ink-3">
                      <span className="font-mono">{alert.entity}</span>
                      <span title={dateTime(alert.created_at)}>{relativeTime(alert.created_at)}</span>
                      <span>{alert.channels.join(', ')}</span>
                      {alert.webhook_status ? <span>webhook {alert.webhook_status}</span> : null}
                      {alert.incident_id ? (
                        <Link to={`/incidents/${alert.incident_id}`} className="text-accent hover:underline">
                          view incident
                        </Link>
                      ) : null}
                    </div>
                  </div>
                  {!alert.acknowledged ? (
                    <button
                      type="button"
                      className="btn btn-sm shrink-0"
                      disabled={update.isPending}
                      onClick={() => update.mutate({ id: alert.alert_id, acknowledged: true })}
                    >
                      <Check className="h-3.5 w-3.5" aria-hidden />
                      Ack
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Rules" subtitle="edit in Settings" dense>
          {rules.isLoading ? (
            <SkeletonRows rows={5} className="p-4" />
          ) : (
            <ul className="divide-y divide-line">
              {(rules.data?.items ?? []).map((rule) => (
                <li key={rule.rule_id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-xs font-medium text-ink">{rule.name}</span>
                    <span
                      className={cn(
                        'chip shrink-0',
                        rule.enabled ? 'border-good/40 text-good' : 'border-line text-ink-3',
                      )}
                    >
                      {rule.enabled ? 'on' : 'off'}
                    </span>
                  </div>
                  <p className="mt-1 text-2xs leading-snug text-ink-3">{rule.description}</p>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {rule.match?.severity_at_least ? (
                      <span className="chip">≥ {rule.match.severity_at_least}</span>
                    ) : null}
                    {(rule.match?.types ?? []).slice(0, 3).map((type) => (
                      <span key={type} className="chip">
                        {titleCase(type)}
                      </span>
                    ))}
                    <span className="chip">cooldown {duration(rule.cooldown_seconds)}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </PageTransition>
  )
}
