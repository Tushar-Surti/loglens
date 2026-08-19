/**
 * KPI tile.
 *
 * A stat tile is the right form when the answer is one number; the sparkline is
 * secondary context, not a chart. Values animate between updates so a live
 * change is noticed without the number flickering.
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { Sparkline } from '@/charts/Sparkline'
import { Delta } from '@/components/Badges'
import { cn } from '@/lib/format'

interface Props {
  label: string
  value: ReactNode
  delta?: number | null
  invertDelta?: boolean
  sparkline?: number[]
  sparklineColor?: string
  hint?: string
  footer?: ReactNode
  emphasis?: 'normal' | 'strong'
  tone?: 'default' | 'good' | 'warn' | 'critical'
  className?: string
}

export function StatTile({
  label,
  value,
  delta,
  invertDelta,
  sparkline,
  sparklineColor,
  hint,
  footer,
  emphasis = 'normal',
  tone = 'default',
  className,
}: Props) {
  const toneClass = {
    default: 'text-ink',
    good: 'text-good',
    warn: 'text-warn',
    critical: 'text-critical',
  }[tone]

  return (
    <div className={cn('flex min-w-0 flex-col justify-between gap-2 px-4 py-3', className)} title={hint}>
      <div className="flex items-start justify-between gap-2">
        <span className="label-caps truncate">{label}</span>
        {delta !== undefined ? <Delta value={delta} invert={invertDelta} /> : null}
      </div>

      <div className="flex items-end justify-between gap-3">
        <span
          className={cn(
            'tnum font-semibold leading-none',
            emphasis === 'strong' ? 'text-3xl' : 'text-2xl',
            toneClass,
          )}
        >
          {value}
        </span>
        {sparkline && sparkline.length > 2 ? (
          <Sparkline values={sparkline} width={84} height={26} color={sparklineColor} ariaLabel={`${label} trend`} />
        ) : null}
      </div>

      {footer ? <div className="text-2xs text-ink-3">{footer}</div> : null}
    </div>
  )
}

/**
 * A single bordered strip of tiles rather than a grid of cards — dense,
 * scannable, and free of the "wall of boxes" look.
 */
export function StatStrip({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'panel grid grid-cols-2 divide-x divide-y divide-line overflow-hidden',
        'sm:grid-cols-3 sm:divide-y-0 lg:grid-cols-6',
        className,
      )}
    >
      {children}
    </div>
  )
}

/** Counts up to a new value; skips the animation for tiny or first changes. */
export function AnimatedNumber({
  value,
  format,
  duration = 480,
}: {
  value: number
  format: (value: number) => string
  duration?: number
}) {
  const [display, setDisplay] = useState(value)
  const fromRef = useRef(value)
  const frameRef = useRef<number>()

  useEffect(() => {
    const from = fromRef.current
    const delta = value - from
    if (!Number.isFinite(delta) || Math.abs(delta) < Math.max(Math.abs(value) * 0.001, 0.0001)) {
      fromRef.current = value
      setDisplay(value)
      return
    }

    const start = performance.now()
    const step = (now: number) => {
      const progress = Math.min((now - start) / duration, 1)
      // easeOutExpo — fast settle, no bounce.
      const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress)
      setDisplay(from + delta * eased)
      if (progress < 1) frameRef.current = requestAnimationFrame(step)
      else fromRef.current = value
    }
    frameRef.current = requestAnimationFrame(step)
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current)
    }
  }, [value, duration])

  return <>{format(display)}</>
}
