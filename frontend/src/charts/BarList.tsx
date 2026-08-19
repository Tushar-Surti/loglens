/**
 * Ranked horizontal bar list — the right form for "top N by magnitude" with
 * long text labels, where a pie or a vertical bar chart would fail.
 *
 * The bar sits *behind* the label rather than beside it: the row stays scannable
 * as text, and the fill encodes share without stealing horizontal space.
 */

import type { ReactNode } from 'react'

import { cn } from '@/lib/format'

export interface BarListItem {
  label: string
  value: number
  display?: ReactNode
  href?: string
  color?: string
  meta?: ReactNode
  onClick?: () => void
}

export function BarList({
  items,
  max,
  emptyMessage = 'No data in this range',
  className,
}: {
  items: BarListItem[]
  max?: number
  emptyMessage?: string
  className?: string
}) {
  const peak = max ?? Math.max(...items.map((item) => item.value), 1)

  if (!items.length) {
    return <p className="px-1 py-6 text-center text-sm text-ink-3">{emptyMessage}</p>
  }

  return (
    <ul className={cn('space-y-1', className)}>
      {items.map((item) => {
        const share = Math.max(2, (item.value / peak) * 100)
        const interactive = Boolean(item.onClick)
        return (
          <li key={item.label}>
            <button
              type="button"
              disabled={!interactive}
              onClick={item.onClick}
              className={cn(
                'group relative flex w-full items-center justify-between gap-3 overflow-hidden rounded-sm px-2 py-1.5 text-left',
                interactive ? 'cursor-pointer hover:bg-raised/60' : 'cursor-default',
              )}
            >
              <span
                className="absolute inset-y-0 left-0 rounded-sm transition-[width] duration-500 ease-out"
                style={{
                  width: `${share}%`,
                  background: item.color ?? 'rgb(var(--accent) / 0.16)',
                  opacity: item.color ? 0.2 : 1,
                }}
                aria-hidden
              />
              <span className="relative min-w-0 flex-1 truncate text-sm text-ink-2 group-hover:text-ink">
                {item.label}
              </span>
              {item.meta ? <span className="relative shrink-0 text-2xs text-ink-3">{item.meta}</span> : null}
              <span className="relative shrink-0 tnum text-sm font-medium text-ink">{item.display ?? item.value}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
