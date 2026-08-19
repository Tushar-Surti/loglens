/** Shared controls: time range, live toggle, segmented switches, search. */

import { motion } from 'framer-motion'
import { Activity, Pause, Search, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/format'
import { RANGE_PRESETS, useUi, type RangeValue } from '@/lib/store'

/**
 * Global time range. One control drives every view — a dashboard where each
 * panel has its own range is a dashboard nobody can reason about.
 */
export function TimeRangePicker() {
  const { range, setRange } = useUi()
  return (
    <div
      className="flex items-center rounded border border-line bg-surface p-0.5"
      role="radiogroup"
      aria-label="Time range"
    >
      {RANGE_PRESETS.map((preset) => {
        const active = preset.value === range
        return (
          <button
            key={preset.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={preset.full}
            onClick={() => setRange(preset.value as RangeValue)}
            className={cn(
              'relative h-6 rounded-xs px-2 text-xs font-medium transition-colors',
              active ? 'text-ink' : 'text-ink-3 hover:text-ink-2',
            )}
          >
            {active ? (
              <motion.span
                layoutId="range-pill"
                className="absolute inset-0 rounded-xs bg-raised"
                transition={{ type: 'spring', stiffness: 420, damping: 34 }}
              />
            ) : null}
            <span className="relative">{preset.label}</span>
          </button>
        )
      })}
    </div>
  )
}

export function LiveToggle() {
  const { live, setLive } = useUi()
  return (
    <button
      type="button"
      onClick={() => setLive(!live)}
      className={cn('btn btn-sm gap-1.5', live && 'border-good/40 text-good')}
      title={live ? 'Streaming — click to pause auto-refresh' : 'Paused — click to resume'}
      aria-pressed={live}
    >
      {live ? (
        <>
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-good opacity-70" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-good" />
          </span>
          Live
        </>
      ) : (
        <>
          <Pause className="h-3 w-3" aria-hidden />
          Paused
        </>
      )}
    </button>
  )
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  size = 'md',
  ariaLabel,
}: {
  options: { value: T; label: ReactNode; title?: string }[]
  value: T
  onChange: (value: T) => void
  size?: 'sm' | 'md'
  ariaLabel?: string
}) {
  const id = useId()
  return (
    <div
      className="inline-flex items-center rounded border border-line bg-surface p-0.5"
      role="radiogroup"
      aria-label={ariaLabel}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={option.title}
            onClick={() => onChange(option.value)}
            className={cn(
              'relative rounded-xs px-2 font-medium transition-colors',
              size === 'sm' ? 'h-5 text-2xs' : 'h-6 text-xs',
              active ? 'text-ink' : 'text-ink-3 hover:text-ink-2',
            )}
          >
            {active ? (
              <motion.span
                layoutId={`segment-${id}`}
                className="absolute inset-0 rounded-xs bg-raised"
                transition={{ type: 'spring', stiffness: 420, damping: 34 }}
              />
            ) : null}
            <span className="relative whitespace-nowrap">{option.label}</span>
          </button>
        )
      })}
    </div>
  )
}

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search…',
  className,
  autoFocus,
  mono,
  onSubmit,
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  autoFocus?: boolean
  mono?: boolean
  onSubmit?: () => void
}) {
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (autoFocus) ref.current?.focus()
  }, [autoFocus])

  return (
    <div className={cn('relative flex min-w-0 items-center', className)}>
      <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-ink-3" aria-hidden />
      <input
        ref={ref}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && onSubmit) onSubmit()
          if (event.key === 'Escape') onChange('')
        }}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        className={cn('input w-full pl-8', value && 'pr-8', mono && 'font-mono text-xs')}
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange('')}
          className="absolute right-2 text-ink-3 transition-colors hover:text-ink"
          aria-label="Clear"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      ) : null}
    </div>
  )
}

/** Small debounced text field — used by search boxes that hit the API. */
export function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}

export function ConnectionBadge({ state }: { state: 'connecting' | 'open' | 'closed' | 'error' }) {
  const map = {
    open: { label: 'connected', tone: 'text-good' },
    connecting: { label: 'connecting', tone: 'text-warn' },
    closed: { label: 'disconnected', tone: 'text-ink-3' },
    error: { label: 'error', tone: 'text-critical' },
  }[state]
  return (
    <span className={cn('inline-flex items-center gap-1 text-2xs', map.tone)}>
      <Activity className="h-3 w-3" aria-hidden />
      {map.label}
    </span>
  )
}
