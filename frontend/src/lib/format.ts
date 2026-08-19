/** Number, duration and time formatting used across the dashboard. */

import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const COMPACT = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
const PLAIN = new Intl.NumberFormat('en')

/** 1_240_000 → "1.2M". Used wherever space is tight (tiles, axes, chips). */
export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (Math.abs(value) < 1000) return Number.isInteger(value) ? String(value) : value.toFixed(1)
  return COMPACT.format(value)
}

export function number(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return PLAIN.format(Number(value.toFixed(digits)))
}

export function percent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/** Latency in ms with a unit that stays readable across four orders of magnitude.
 *
 * The unit is chosen from the *magnitude*, with the sign reapplied afterwards.
 * Deciding on the raw value made every negative number take the microsecond
 * branch, so an ingest latency of −145 ms rendered as "−144500µs".
 */
export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const sign = value < 0 ? '-' : ''
  const magnitude = Math.abs(value)
  if (magnitude < 1) return `${sign}${(magnitude * 1000).toFixed(0)}µs`
  if (magnitude < 1000) return `${sign}${magnitude.toFixed(magnitude < 10 ? 1 : 0)}ms`
  return `${sign}${(magnitude / 1000).toFixed(2)}s`
}

export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size < 10 && unit > 0 ? 1 : 0)} ${units[unit]}`
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const delta = (Date.now() - then) / 1000
  if (delta < 0) return 'in the future'
  if (delta < 10) return 'just now'
  if (delta < 60) return `${Math.floor(delta)}s ago`
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`
  return `${Math.floor(delta / 86400)}d ago`
}

export function clock(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}),
    hour12: false,
  })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** Axis tick formatter: shows the unit that actually varies across the range. */
export function axisTime(iso: string, bucketSeconds: number): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  if (bucketSeconds >= 86400) return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
  if (bucketSeconds >= 3600) {
    return date.toLocaleString([], { day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  }
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

export function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
}

export function titleCase(value: string): string {
  return value.replace(/[_-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function truncate(value: string, max = 48): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`
}

/** Status-class colour role. Reserved status hues, never the series palette. */
export function statusRole(status: number): 'good' | 'accent' | 'warn' | 'critical' {
  if (status >= 500) return 'critical'
  if (status >= 400) return 'warn'
  if (status >= 300) return 'accent'
  return 'good'
}
