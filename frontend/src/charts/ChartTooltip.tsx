/** Shared tooltip surface for every Recharts chart. */

import type { ReactNode } from 'react'

import { cn, dateTime } from '@/lib/format'

interface TooltipRow {
  label: string
  value: ReactNode
  color?: string
  muted?: boolean
}

export function TooltipCard({
  title,
  rows,
  footer,
  className,
}: {
  title?: ReactNode
  rows: TooltipRow[]
  footer?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'min-w-[168px] rounded border border-line-strong bg-surface/95 backdrop-blur-sm',
        'px-3 py-2 shadow-lg shadow-black/20',
        className,
      )}
    >
      {title ? <div className="mb-1.5 text-2xs font-medium tracking-wide text-ink-3">{title}</div> : null}
      <div className="space-y-1">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-6 text-xs">
            <span className="flex items-center gap-1.5 text-ink-2">
              {row.color ? (
                <span
                  className="h-2 w-2 shrink-0 rounded-[2px]"
                  style={{ background: row.color }}
                  aria-hidden
                />
              ) : null}
              {row.label}
            </span>
            <span className={cn('tnum font-medium', row.muted ? 'text-ink-3' : 'text-ink')}>{row.value}</span>
          </div>
        ))}
      </div>
      {footer ? <div className="mt-2 border-t border-line pt-1.5 text-2xs text-ink-3">{footer}</div> : null}
    </div>
  )
}

/**
 * Recharts tooltip adapter.
 *
 * `format` maps a series key to a display value so each chart controls units
 * without re-implementing the tooltip chrome.
 */
export function SeriesTooltip({
  active,
  payload,
  label,
  format,
  labelFormat,
  total,
}: {
  active?: boolean
  payload?: any[]
  label?: any
  format?: (key: string, value: number) => ReactNode
  labelFormat?: (label: any) => ReactNode
  total?: boolean
}) {
  if (!active || !payload?.length) return null

  const rows: TooltipRow[] = payload
    .filter((entry) => entry.value !== null && entry.value !== undefined)
    .map((entry) => ({
      label: entry.name ?? entry.dataKey,
      value: format ? format(entry.dataKey, entry.value) : entry.value,
      color: entry.stroke && entry.stroke !== 'none' ? entry.stroke : entry.fill,
    }))

  const sum = payload.reduce((acc, entry) => acc + (Number(entry.value) || 0), 0)

  return (
    <TooltipCard
      title={labelFormat ? labelFormat(label) : dateTime(label)}
      rows={rows}
      footer={total ? `Total ${sum.toLocaleString()}` : undefined}
    />
  )
}
