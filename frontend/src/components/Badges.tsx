/**
 * Status vocabulary.
 *
 * Every status colour ships with a label or an icon — colour never carries the
 * meaning on its own, which is both an accessibility requirement and the only
 * way a severity reads correctly on a projector or a printed screenshot.
 */

import {
  AlertOctagon,
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDot,
  Info,
  ShieldAlert,
} from 'lucide-react'
import type { ReactNode } from 'react'

import { cn, statusRole } from '@/lib/format'
import type { HealthStatus, Severity } from '@/lib/types'

const SEVERITY_STYLES: Record<Severity, { chip: string; dot: string; icon: ReactNode; label: string }> = {
  critical: {
    chip: 'border-critical/40 bg-critical/12 text-critical',
    dot: 'bg-critical',
    icon: <AlertOctagon className="h-3 w-3" aria-hidden />,
    label: 'Critical',
  },
  high: {
    chip: 'border-serious/40 bg-serious/12 text-serious',
    dot: 'bg-serious',
    icon: <AlertTriangle className="h-3 w-3" aria-hidden />,
    label: 'High',
  },
  medium: {
    chip: 'border-warn/40 bg-warn/12 text-warn',
    dot: 'bg-warn',
    icon: <ShieldAlert className="h-3 w-3" aria-hidden />,
    label: 'Medium',
  },
  low: {
    chip: 'border-good/40 bg-good/12 text-good',
    dot: 'bg-good',
    icon: <CircleDot className="h-3 w-3" aria-hidden />,
    label: 'Low',
  },
  info: {
    chip: 'border-line bg-raised text-ink-2',
    dot: 'bg-ink-3',
    icon: <Info className="h-3 w-3" aria-hidden />,
    label: 'Info',
  },
}

export function SeverityBadge({
  severity,
  score,
  size = 'md',
}: {
  severity: Severity
  score?: number
  size?: 'sm' | 'md'
}) {
  const style = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.info
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-sm border font-medium',
        style.chip,
        size === 'sm' ? 'h-5 px-1.5 text-2xs' : 'h-6 px-2 text-xs',
      )}
    >
      {style.icon}
      {style.label}
      {score !== undefined ? <span className="tnum opacity-75">{score.toFixed(0)}</span> : null}
    </span>
  )
}

export function SeverityDot({ severity, pulse }: { severity: Severity; pulse?: boolean }) {
  const style = SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.info
  return (
    <span
      className={cn('inline-block h-2 w-2 shrink-0 rounded-full', style.dot, pulse && 'animate-pulse-ring')}
      title={style.label}
      aria-label={style.label}
    />
  )
}

const HEALTH_STYLES: Record<string, { color: string; label: string }> = {
  healthy: { color: 'bg-good', label: 'Healthy' },
  ok: { color: 'bg-good', label: 'Healthy' },
  degraded: { color: 'bg-warn', label: 'Degraded' },
  stale: { color: 'bg-warn', label: 'Stale' },
  critical: { color: 'bg-critical', label: 'Critical' },
  stalled: { color: 'bg-critical', label: 'Stalled' },
  no_data: { color: 'bg-ink-3', label: 'No data' },
  unknown: { color: 'bg-ink-3', label: 'Unknown' },
  unavailable: { color: 'bg-critical', label: 'Unavailable' },
}

export function HealthPill({ status, label }: { status: HealthStatus | string; label?: string }) {
  const style = HEALTH_STYLES[status] ?? HEALTH_STYLES.unknown
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-line bg-raised px-2 py-0.5 text-2xs font-medium text-ink-2">
      <span className={cn('h-1.5 w-1.5 rounded-full', style.color)} aria-hidden />
      {label ?? style.label}
    </span>
  )
}

export function StatusCode({ code }: { code: number }) {
  const role = statusRole(code)
  const tone = {
    good: 'text-good',
    accent: 'text-accent',
    warn: 'text-warn',
    critical: 'text-critical',
  }[role]
  return <span className={cn('tnum font-mono text-xs font-medium', tone)}>{code}</span>
}

const METHOD_TONE: Record<string, string> = {
  GET: 'text-series-1 border-series-1/30',
  POST: 'text-series-3 border-series-3/30',
  PUT: 'text-series-4 border-series-4/30',
  PATCH: 'text-series-5 border-series-5/30',
  DELETE: 'text-series-8 border-series-8/30',
}

export function MethodBadge({ method }: { method: string }) {
  return (
    <span
      className={cn(
        'inline-flex h-5 items-center rounded-xs border bg-transparent px-1.5 font-mono text-2xs font-semibold',
        METHOD_TONE[method] ?? 'text-ink-3 border-line',
      )}
    >
      {method}
    </span>
  )
}

export function BotBadge({ isBot }: { isBot?: boolean }) {
  if (!isBot) return null
  return (
    <span className="inline-flex items-center gap-1 rounded-xs border border-line bg-raised px-1.5 py-px text-2xs text-ink-3">
      <Bot className="h-3 w-3" aria-hidden />
      bot
    </span>
  )
}

/** Ground-truth marker: this incident was injected, so detection can be judged. */
export function GroundTruthBadge({ label }: { label?: string | null }) {
  if (!label) return null
  return (
    <span
      className="inline-flex items-center gap-1 rounded-xs border border-accent/40 bg-accent/10 px-1.5 py-px text-2xs font-medium text-accent"
      title="This window contains a deliberately injected incident — used to score detection quality"
    >
      <CheckCircle2 className="h-3 w-3" aria-hidden />
      {label.replace(/_/g, ' ')}
    </span>
  )
}

export function Delta({ value, invert = false }: { value: number | null | undefined; invert?: boolean }) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <span className="text-2xs text-ink-3">—</span>
  }
  const rising = value > 0
  // "Good" depends on the metric: more traffic is usually fine, more latency is not.
  const good = invert ? !rising : rising
  const neutral = Math.abs(value) < 1
  return (
    <span
      className={cn(
        'tnum text-2xs font-medium',
        neutral ? 'text-ink-3' : good ? 'text-good' : 'text-critical',
      )}
      title="vs the previous period of equal length"
    >
      {rising ? '▲' : '▼'} {Math.abs(value).toFixed(1)}%
    </span>
  )
}
