/**
 * The workhorse time-series chart.
 *
 * Design decisions that are not negotiable here:
 *  - one y-axis, ever. Two measures of different scale become two charts.
 *  - 2px strokes, no dots except on hover, recessive grid, no chart junk.
 *  - a legend whenever there are ≥2 series, plus a direct label on the last
 *    point when there are ≤4 — identity never rests on colour alone.
 *  - anomaly windows are drawn as translucent status-coloured bands behind the
 *    data, so an incident is visible in the same glance as the metric.
 */

import { useMemo } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { SeriesTooltip } from '@/charts/ChartTooltip'
import { severityColor, useChartTheme } from '@/charts/palette'
import { axisTime, compact, dateTime } from '@/lib/format'

export interface SeriesSpec {
  key: string
  label: string
  color?: string
  strokeDasharray?: string
}

export interface AnomalyBand {
  start: string
  end: string
  severity: string
  label?: string
}

interface Props {
  data: Record<string, any>[]
  series: SeriesSpec[]
  xKey?: string
  height?: number
  variant?: 'line' | 'area'
  bucketSeconds?: number
  formatValue?: (key: string, value: number) => string
  formatAxis?: (value: number) => string
  anomalies?: AnomalyBand[]
  threshold?: { value: number; label: string }
  syncId?: string
  showLegend?: boolean
  yDomain?: [number | 'auto' | 'dataMin' | 'dataMax', number | 'auto' | 'dataMin' | 'dataMax']
  className?: string
}

export function TimeSeriesChart({
  data,
  series,
  xKey = 't',
  height = 240,
  variant = 'line',
  bucketSeconds = 60,
  formatValue,
  formatAxis = compact,
  anomalies = [],
  threshold,
  syncId,
  showLegend = true,
  yDomain,
  className,
}: Props) {
  const theme = useChartTheme()

  const resolved = useMemo(
    () => series.map((spec, index) => ({ ...spec, color: spec.color ?? theme.series[index % theme.series.length] })),
    [series, theme],
  )

  // A numeric (time) x-axis rather than a category axis: gaps in the data stay
  // proportional, and anomaly bands can be placed at arbitrary instants instead
  // of only on existing categories.
  const points = useMemo<Record<string, any>[]>(
    () => data.map((row) => ({ ...row, __x: new Date(row[xKey]).getTime() })).filter((row) => Number.isFinite(row.__x)),
    [data, xKey],
  )
  const bands = useMemo(
    () =>
      anomalies
        .map((band) => ({
          ...band,
          x1: new Date(band.start).getTime(),
          x2: new Date(band.end).getTime(),
        }))
        .filter((band) => Number.isFinite(band.x1) && Number.isFinite(band.x2)),
    [anomalies],
  )

  // Direct labels only when the chart is legible with them (≤4 series and the
  // last point exists) — a label on every point is noise, not information.
  const lastPoint = points.length ? points[points.length - 1] : undefined
  const directLabels = resolved.length <= 4 && lastPoint

  const Chart = variant === 'area' ? AreaChart : LineChart

  return (
    <div className={className}>
      {showLegend && resolved.length > 1 ? (
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 px-1">
          {resolved.map((spec) => (
            <span key={spec.key} className="flex items-center gap-1.5 text-xs text-ink-2">
              <span className="h-[2px] w-3 rounded-full" style={{ background: spec.color }} aria-hidden />
              {spec.label}
              {directLabels && lastPoint?.[spec.key] !== undefined ? (
                <span className="tnum text-ink-3">
                  {formatValue ? formatValue(spec.key, lastPoint[spec.key]) : compact(lastPoint[spec.key])}
                </span>
              ) : null}
            </span>
          ))}
        </div>
      ) : null}

      <ResponsiveContainer width="100%" height={height}>
        <Chart data={points} syncId={syncId} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
          <defs>
            {resolved.map((spec) => (
              <linearGradient key={spec.key} id={`fill-${spec.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={spec.color} stopOpacity={0.22} />
                <stop offset="100%" stopColor={spec.color} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>

          <CartesianGrid strokeDasharray="0" vertical={false} stroke={theme.grid} />

          <XAxis
            dataKey="__x"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(value) => axisTime(new Date(value).toISOString(), bucketSeconds)}
            axisLine={{ stroke: theme.grid }}
            tickLine={false}
            minTickGap={44}
            tickMargin={8}
          />
          <YAxis
            tickFormatter={formatAxis}
            axisLine={false}
            tickLine={false}
            width={48}
            tickMargin={4}
            domain={yDomain}
          />

          {bands.map((band, index) => (
            <ReferenceArea
              key={`${band.start}-${index}`}
              x1={band.x1}
              x2={band.x2}
              fill={severityColor(band.severity, theme)}
              fillOpacity={0.13}
              stroke={severityColor(band.severity, theme)}
              strokeOpacity={0.28}
              ifOverflow="extendDomain"
            />
          ))}

          {threshold ? (
            <ReferenceLine
              y={threshold.value}
              stroke={theme.status.warn}
              strokeDasharray="4 4"
              strokeWidth={1}
              label={{ value: threshold.label, position: 'insideTopRight', fill: theme.ink3, fontSize: 10 }}
            />
          ) : null}

          <Tooltip
            content={
              <SeriesTooltip
                format={(key, value) => (formatValue ? formatValue(key, value) : compact(value))}
                labelFormat={(value) => dateTime(new Date(value).toISOString())}
              />
            }
            cursor={{ stroke: theme.axis, strokeWidth: 1 }}
            isAnimationActive={false}
          />

          {resolved.map((spec) =>
            variant === 'area' ? (
              <Area
                key={spec.key}
                type="monotone"
                dataKey={spec.key}
                name={spec.label}
                stroke={spec.color}
                strokeWidth={2}
                fill={`url(#fill-${spec.key})`}
                dot={false}
                activeDot={{ r: 3.5, strokeWidth: 2, stroke: theme.surface }}
                isAnimationActive={false}
                connectNulls
              />
            ) : (
              <Line
                key={spec.key}
                type="monotone"
                dataKey={spec.key}
                name={spec.label}
                stroke={spec.color}
                strokeWidth={2}
                strokeDasharray={spec.strokeDasharray}
                dot={false}
                activeDot={{ r: 3.5, strokeWidth: 2, stroke: theme.surface }}
                isAnimationActive={false}
                connectNulls
              />
            ),
          )}
        </Chart>
      </ResponsiveContainer>
    </div>
  )
}
