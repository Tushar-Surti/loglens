/**
 * Stacked composition of HTTP status classes over time.
 *
 * Status classes get the reserved status hues (2xx good, 4xx warning,
 * 5xx critical) — never categorical series slots — and each stacked segment
 * carries a 2px surface-coloured separator so adjacent fills stay legible in
 * colour-vision-deficient viewing.
 */

import { useMemo } from 'react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { SeriesTooltip } from '@/charts/ChartTooltip'
import { statusClassColor, useChartTheme } from '@/charts/palette'
import { axisTime, compact, dateTime } from '@/lib/format'

const CLASS_ORDER = ['2xx', '3xx', '4xx', '5xx', '1xx']

export function StackedStatusChart({
  data,
  classes,
  height = 220,
  bucketSeconds = 60,
  xKey = 't',
  syncId,
}: {
  data: Record<string, any>[]
  classes: string[]
  height?: number
  bucketSeconds?: number
  xKey?: string
  syncId?: string
}) {
  const theme = useChartTheme()
  const ordered = useMemo(
    () => CLASS_ORDER.filter((item) => classes.includes(item)).concat(classes.filter((c) => !CLASS_ORDER.includes(c))),
    [classes],
  )
  // Numeric time axis so gaps in the stream stay proportional.
  const points = useMemo<Record<string, any>[]>(
    () => data.map((row) => ({ ...row, __x: new Date(row[xKey]).getTime() })).filter((row) => Number.isFinite(row.__x)),
    [data, xKey],
  )

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 px-1">
        {ordered.map((statusClass) => (
          <span key={statusClass} className="flex items-center gap-1.5 text-xs text-ink-2">
            <span
              className="h-2 w-2 rounded-[2px]"
              style={{ background: statusClassColor(statusClass, theme) }}
              aria-hidden
            />
            {statusClass}
          </span>
        ))}
      </div>

      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={points} syncId={syncId} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
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
          <YAxis tickFormatter={compact} axisLine={false} tickLine={false} width={48} tickMargin={4} />
          <Tooltip
            content={<SeriesTooltip format={(_key, value) => compact(value)} labelFormat={(value) => dateTime(new Date(value).toISOString())} total />}
            cursor={{ stroke: theme.axis, strokeWidth: 1 }}
            isAnimationActive={false}
          />
          {ordered.map((statusClass) => (
            <Area
              key={statusClass}
              type="monotone"
              dataKey={statusClass}
              name={statusClass}
              stackId="status"
              stroke={theme.surface}
              strokeWidth={1.5}
              fill={statusClassColor(statusClass, theme)}
              fillOpacity={0.85}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
