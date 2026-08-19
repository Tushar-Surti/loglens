/**
 * Settings — detector thresholds, alert rules and appearance.
 *
 * Threshold edits are validated client-side against the server's own bounds and
 * take effect on the running detectors within ~30 seconds; nothing needs a
 * redeploy.
 */

import { Check, RotateCcw, Save, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { SegmentedControl } from '@/components/Controls'
import { PageHeader } from '@/components/PageHeader'
import { ErrorState, Panel, PageTransition, SkeletonRows } from '@/components/Panel'
import {
  useAlertRules,
  useResetThresholds,
  useSaveAlertRule,
  useSaveThresholds,
  useThresholds,
} from '@/hooks/useApi'
import { cn, duration, relativeTime, titleCase } from '@/lib/format'
import { useUi } from '@/lib/store'

export function Settings() {
  const thresholds = useThresholds()
  const save = useSaveThresholds()
  const reset = useResetThresholds()
  const rules = useAlertRules()
  const saveRule = useSaveAlertRule()
  const { theme, setTheme, live, setLive } = useUi()

  const [draft, setDraft] = useState<Record<string, number>>({})

  useEffect(() => {
    if (thresholds.data?.values) setDraft(thresholds.data.values)
  }, [thresholds.data])

  const dirty = useMemo(() => {
    if (!thresholds.data) return false
    return Object.entries(draft).some(([key, value]) => thresholds.data!.values[key] !== value)
  }, [draft, thresholds.data])

  const severityOrderValid = useMemo(() => {
    const order = ['severity_low', 'severity_medium', 'severity_high', 'severity_critical']
    const values = order.map((key) => draft[key] ?? 0)
    return values.every((value, index) => index === 0 || value > values[index - 1])
  }, [draft])

  if (thresholds.isLoading) return <SkeletonRows rows={12} className="p-4" />
  if (thresholds.error) return <ErrorState error={thresholds.error} onRetry={thresholds.refetch} />

  const meta = thresholds.data?.meta ?? {}
  const bounds = thresholds.data?.bounds ?? {}
  const defaults = thresholds.data?.defaults ?? {}

  const groups: { title: string; description: string; keys: string[] }[] = [
    {
      title: 'Sensitivity',
      description: 'How far a window must depart from its own history before a detector speaks.',
      keys: ['zscore_threshold', 'ewma_alpha', 'min_history_points', 'isoforest_contamination'],
    },
    {
      title: 'Service level',
      description: 'Absolute budgets, applied regardless of what the baseline says is normal.',
      keys: ['error_rate_threshold', 'latency_p95_multiplier'],
    },
    {
      title: 'Abuse detection',
      description: 'Per-source limits behind the volumetric, scanning and credential-stuffing rules.',
      keys: ['ip_rps_threshold', 'scan_unique_paths', 'auth_fail_threshold'],
    },
    {
      title: 'Severity ladder',
      description: 'Fused 0–100 score cut-points. Anomalies below the reporting floor are never stored.',
      keys: ['severity_low', 'severity_medium', 'severity_high', 'severity_critical'],
    },
  ]

  return (
    <PageTransition>
      <PageHeader
        title="Settings"
        description="Tune detection without redeploying. Changes are picked up by the streaming detectors within 30 seconds."
        actions={
          <>
            <button
              type="button"
              className="btn btn-sm"
              disabled={reset.isPending}
              onClick={() => reset.mutate()}
              title="Discard overrides and return to environment defaults"
            >
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              Reset to defaults
            </button>
            <button
              type="button"
              className="btn btn-sm btn-accent"
              disabled={!dirty || !severityOrderValid || save.isPending}
              onClick={() => save.mutate(draft)}
            >
              {save.isSuccess && !dirty ? <Check className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" />}
              {save.isPending ? 'Saving…' : dirty ? 'Save changes' : 'Saved'}
            </button>
          </>
        }
      />

      {!severityOrderValid ? (
        <div className="mb-4 flex items-start gap-2 rounded border border-warn/40 bg-warn/8 px-3 py-2 text-xs text-warn">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          Severity thresholds must increase: reporting floor &lt; medium &lt; high &lt; critical.
        </div>
      ) : null}

      {save.isError ? (
        <div className="mb-4 rounded border border-critical/40 bg-critical/8 px-3 py-2 text-xs text-critical">
          {JSON.stringify((save.error as any)?.detail ?? (save.error as Error).message)}
        </div>
      ) : null}

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        {groups.map((group) => (
          <Panel key={group.title} title={group.title} subtitle={group.description}>
            <div className="space-y-4">
              {group.keys.map((key) => {
                const bound = bounds[key] ?? { min: 0, max: 100 }
                const value = draft[key] ?? defaults[key] ?? 0
                const isDefault = value === defaults[key]
                const step = bound.max <= 1 ? 0.01 : bound.max <= 20 ? 0.1 : 1
                return (
                  <div key={key}>
                    <div className="flex items-baseline justify-between gap-3">
                      <label htmlFor={key} className="text-xs font-medium text-ink">
                        {meta[key]?.label ?? titleCase(key)}
                      </label>
                      <div className="flex items-center gap-2">
                        {!isDefault ? <span className="chip border-accent/40 text-accent">overridden</span> : null}
                        <input
                          id={key}
                          type="number"
                          className="input h-6 w-20 text-right text-xs"
                          value={value}
                          min={bound.min}
                          max={bound.max}
                          step={step}
                          onChange={(event) =>
                            setDraft((current) => ({ ...current, [key]: Number(event.target.value) }))
                          }
                        />
                      </div>
                    </div>
                    <input
                      type="range"
                      className="mt-2 w-full accent-[rgb(var(--accent))]"
                      value={value}
                      min={bound.min}
                      max={bound.max}
                      step={step}
                      onChange={(event) => setDraft((current) => ({ ...current, [key]: Number(event.target.value) }))}
                      aria-label={meta[key]?.label ?? key}
                    />
                    <p className="mt-1 text-2xs leading-snug text-ink-3">{meta[key]?.help}</p>
                    <p className="mt-0.5 text-2xs text-ink-3/70">
                      range {bound.min}–{bound.max} · default {defaults[key]}
                    </p>
                  </div>
                )
              })}
            </div>
          </Panel>
        ))}
      </div>

      <Panel
        title="Alert rules"
        subtitle="which detections page, and how often"
        className="mb-4"
        dense
      >
        {rules.isLoading ? (
          <SkeletonRows rows={5} className="p-4" />
        ) : (
          <ul className="divide-y divide-line">
            {(rules.data?.items ?? []).map((rule) => (
              <li key={rule.rule_id} className="flex items-start gap-3 px-4 py-3">
                <button
                  type="button"
                  role="switch"
                  aria-checked={rule.enabled}
                  aria-label={`${rule.enabled ? 'Disable' : 'Enable'} ${rule.name}`}
                  onClick={() => saveRule.mutate({ ruleId: rule.rule_id, enabled: !rule.enabled })}
                  className={cn(
                    'mt-0.5 h-4 w-7 shrink-0 rounded-full border transition-colors',
                    rule.enabled ? 'border-accent bg-accent/80' : 'border-line bg-raised',
                  )}
                >
                  <span
                    className={cn(
                      'block h-3 w-3 rounded-full bg-white transition-transform',
                      rule.enabled ? 'translate-x-3.5' : 'translate-x-0.5',
                    )}
                  />
                </button>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-ink">{rule.name}</p>
                  <p className="mt-0.5 text-xs text-ink-3">{rule.description}</p>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {rule.match?.severity_at_least ? (
                      <span className="chip">severity ≥ {rule.match.severity_at_least}</span>
                    ) : null}
                    {(rule.match?.types ?? []).map((type) => (
                      <span key={type} className="chip">
                        {titleCase(type)}
                      </span>
                    ))}
                    {(rule.channels ?? []).map((channel) => (
                      <span key={channel} className="chip border-accent/30 text-accent">
                        {channel}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  <p className="label-caps">Cooldown</p>
                  <p className="tnum mt-0.5 text-xs text-ink-2">{duration(rule.cooldown_seconds)}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Appearance">
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-xs font-medium text-ink">Theme</p>
                <p className="mt-0.5 text-2xs text-ink-3">Dark is designed for wall displays; light for daylight desks.</p>
              </div>
              <SegmentedControl
                options={[
                  { value: 'dark', label: 'Dark' },
                  { value: 'light', label: 'Light' },
                ]}
                value={theme}
                onChange={(value) => setTheme(value as 'dark' | 'light')}
                ariaLabel="Theme"
              />
            </div>
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-xs font-medium text-ink">Live updates</p>
                <p className="mt-0.5 text-2xs text-ink-3">
                  Polling cadence adapts to the selected range: 5s at 15m, 2m at 7d.
                </p>
              </div>
              <SegmentedControl
                options={[
                  { value: 'on', label: 'On' },
                  { value: 'off', label: 'Off' },
                ]}
                value={live ? 'on' : 'off'}
                onChange={(value) => setLive(value === 'on')}
                ariaLabel="Live updates"
              />
            </div>
          </div>
        </Panel>

        <Panel title="About this deployment">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
            <div>
              <dt className="label-caps">Thresholds updated</dt>
              <dd className="mt-0.5 text-ink-2">
                {thresholds.data?.updated_at ? relativeTime(thresholds.data.updated_at) : 'using defaults'}
              </dd>
            </div>
            <div>
              <dt className="label-caps">Overrides active</dt>
              <dd className="mt-0.5 text-ink-2">{Object.keys(thresholds.data?.overrides ?? {}).length}</dd>
            </div>
            <div className="col-span-2">
              <dt className="label-caps">Pipeline</dt>
              <dd className="mt-0.5 leading-relaxed text-ink-2">
                Log sources → Kafka → Spark Structured Streaming (windowed aggregation, session windows) →
                statistical + IsolationForest detection → MongoDB → this dashboard.
              </dd>
            </div>
          </dl>
        </Panel>
      </div>
    </PageTransition>
  )
}
