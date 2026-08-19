/**
 * Chart colour access.
 *
 * Charts need concrete colour strings (SVG attributes cannot take
 * `rgb(var(--x))` reliably across libraries), but the tokens must stay the
 * single source of truth. This module resolves the CSS custom properties to
 * hex-ish `rgb()` strings and re-resolves them when the theme changes.
 *
 * Rules enforced by construction:
 *  - categorical hues are assigned in FIXED slot order, never cycled;
 *  - status hues (good/warn/serious/critical) are reserved and never used as
 *    a series colour;
 *  - a 9th series folds into "Other" rather than inventing a hue.
 */

import { useEffect, useState } from 'react'

import { useUi } from '@/lib/store'

const SERIES_SLOTS = 8

function readVar(name: string): string {
  if (typeof window === 'undefined') return 'rgb(120,120,120)'
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value ? `rgb(${value})` : 'rgb(120,120,120)'
}

function readVarAlpha(name: string, alpha: number): string {
  if (typeof window === 'undefined') return `rgba(120,120,120,${alpha})`
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value ? `rgb(${value} / ${alpha})` : `rgba(120,120,120,${alpha})`
}

export interface ChartTheme {
  series: string[]
  status: { good: string; warn: string; serious: string; critical: string }
  ink: string
  ink2: string
  ink3: string
  grid: string
  axis: string
  surface: string
  accent: string
  alpha: (name: string, value: number) => string
}

function build(): ChartTheme {
  return {
    series: Array.from({ length: SERIES_SLOTS }, (_, index) => readVar(`--series-${index + 1}`)),
    status: {
      good: readVar('--good'),
      warn: readVar('--warn'),
      serious: readVar('--serious'),
      critical: readVar('--critical'),
    },
    ink: readVar('--ink'),
    ink2: readVar('--ink-2'),
    ink3: readVar('--ink-3'),
    grid: readVar('--grid'),
    axis: readVar('--line-strong'),
    surface: readVar('--surface'),
    accent: readVar('--accent'),
    alpha: readVarAlpha,
  }
}

/** Re-resolves the palette whenever the theme flips. */
export function useChartTheme(): ChartTheme {
  const theme = useUi((state) => state.theme)
  const [palette, setPalette] = useState<ChartTheme>(() => build())

  useEffect(() => {
    // Next frame, so the `dark` class is already on <html>.
    const id = requestAnimationFrame(() => setPalette(build()))
    return () => cancelAnimationFrame(id)
  }, [theme])

  return palette
}

/** HTTP status classes carry *status* semantics, not series identity. */
export function statusClassColor(statusClass: string, theme: ChartTheme): string {
  switch (statusClass) {
    case '2xx':
      return theme.status.good
    case '3xx':
      return theme.series[0]
    case '4xx':
      return theme.status.warn
    case '5xx':
      return theme.status.critical
    default:
      return theme.ink3
  }
}

export const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info'] as const

export function severityColor(severity: string, theme: ChartTheme): string {
  switch (severity) {
    case 'critical':
      return theme.status.critical
    case 'high':
      return theme.status.serious
    case 'medium':
      return theme.status.warn
    case 'low':
      return theme.status.good
    default:
      return theme.ink3
  }
}

/** Sequential blue ramp for magnitude encodings (heatmaps). One hue, light→dark. */
export const SEQUENTIAL_LIGHT = ['#e8f0fb', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95']
export const SEQUENTIAL_DARK = ['#12233a', '#104281', '#184f95', '#1c5cab', '#256abf', '#3987e5', '#6da7ec']

export function sequentialScale(value: number, dark: boolean): string {
  const ramp = dark ? SEQUENTIAL_DARK : SEQUENTIAL_LIGHT
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0))
  return ramp[Math.min(ramp.length - 1, Math.floor(clamped * ramp.length))]
}
