/**
 * Inline sparkline for table rows.
 *
 * Hand-rolled SVG rather than a chart library: these render dozens per table
 * and a full Recharts instance each would cost more than the whole page.
 * No axes, no tooltip — a sparkline shows shape, and the row shows the number.
 */

import { useMemo } from 'react'

import { useChartTheme } from '@/charts/palette'

interface Props {
  values: number[]
  width?: number
  height?: number
  color?: string
  fill?: boolean
  strokeWidth?: number
  ariaLabel?: string
}

export function Sparkline({ values, width = 96, height = 24, color, fill = true, strokeWidth = 1.5, ariaLabel }: Props) {
  const theme = useChartTheme()
  const stroke = color ?? theme.series[0]

  const { line, area, last } = useMemo(() => {
    const clean = values.filter((value) => Number.isFinite(value))
    if (clean.length < 2) return { line: '', area: '', last: null as null | { x: number; y: number } }

    const min = Math.min(...clean)
    const max = Math.max(...clean)
    const span = max - min || 1
    const stepX = width / (clean.length - 1)
    const pad = strokeWidth + 0.5

    const points = clean.map((value, index) => {
      const x = index * stepX
      const y = height - pad - ((value - min) / span) * (height - pad * 2)
      return [x, y] as const
    })

    const path = points.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
    const areaPath = `${path} L${width},${height} L0,${height} Z`
    const [lastX, lastY] = points[points.length - 1]
    return { line: path, area: areaPath, last: { x: lastX, y: lastY } }
  }, [values, width, height, strokeWidth])

  if (!line) {
    return <div className="h-6 w-24 rounded-sm bg-raised/60" aria-hidden />
  }

  const gradientId = `spark-${stroke.replace(/[^a-z0-9]/gi, '')}`

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="overflow-visible"
      role="img"
      aria-label={ariaLabel ?? 'trend'}
    >
      {fill ? (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.24} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradientId})`} />
        </>
      ) : null}
      <path d={line} fill="none" stroke={stroke} strokeWidth={strokeWidth} strokeLinejoin="round" strokeLinecap="round" />
      {last ? <circle cx={last.x} cy={last.y} r={1.8} fill={stroke} /> : null}
    </svg>
  )
}
