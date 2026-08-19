/**
 * Day-of-week × hour traffic heatmap.
 *
 * Magnitude → one sequential hue, light→dark (never a rainbow). Cells carry a
 * 1px surface gap so the grid reads as discrete buckets, and every cell has a
 * title so the values are reachable without hovering a tooltip library.
 */

import { useMemo, useState } from 'react'

import { TooltipCard } from '@/charts/ChartTooltip'
import { sequentialScale } from '@/charts/palette'
import { compact, ms, percent } from '@/lib/format'
import { useUi } from '@/lib/store'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export interface HeatCell {
  day: number
  hour: number
  requests: number
  rps: number
  error_rate: number
  p95_response_time: number
  windows: number
}

export function Heatmap({ cells, metric = 'requests' }: { cells: HeatCell[]; metric?: 'requests' | 'error_rate' | 'p95_response_time' }) {
  const dark = useUi((state) => state.theme) === 'dark'
  const [hovered, setHovered] = useState<HeatCell | null>(null)

  const { lookup, max, activeDays } = useMemo(() => {
    const map = new Map<string, HeatCell>()
    let peak = 0
    const days = new Set<number>()
    for (const cell of cells) {
      map.set(`${cell.day}-${cell.hour}`, cell)
      days.add(cell.day)
      const value = Number(cell[metric] ?? 0)
      if (value > peak) peak = value
    }
    return { lookup: map, max: peak || 1, activeDays: days }
  }, [cells, metric])

  return (
    <div className="relative">
      <div className="flex gap-1.5">
        <div className="flex w-8 shrink-0 flex-col justify-around pt-4">
          {DAYS.map((day, index) => (
            <span
              key={day}
              className={`h-4 text-2xs leading-4 ${activeDays.has(index) ? 'text-ink-3' : 'text-ink-3/40'}`}
            >
              {day}
            </span>
          ))}
        </div>

        <div className="min-w-0 flex-1 overflow-x-auto">
          <div className="min-w-[520px]">
            <div className="mb-1 flex gap-[2px]">
              {Array.from({ length: 24 }, (_, hour) => (
                <div key={hour} className="flex-1 text-center text-2xs text-ink-3">
                  {hour % 3 === 0 ? String(hour).padStart(2, '0') : ''}
                </div>
              ))}
            </div>

            <div className="flex flex-col gap-[2px]">
              {DAYS.map((_, day) => (
                <div key={day} className="flex gap-[2px]">
                  {Array.from({ length: 24 }, (_, hour) => {
                    const cell = lookup.get(`${day}-${hour}`)
                    const value = cell ? Number(cell[metric] ?? 0) : 0
                    return (
                      <div
                        key={hour}
                        className="h-4 flex-1 rounded-[2px] transition-transform duration-100 hover:scale-[1.18]"
                        style={{
                          background: cell ? sequentialScale(value / max, dark) : 'rgb(var(--raised) / 0.5)',
                        }}
                        onMouseEnter={() => cell && setHovered(cell)}
                        onMouseLeave={() => setHovered(null)}
                        title={
                          cell
                            ? `${DAYS[day]} ${String(hour).padStart(2, '0')}:00 — ${compact(cell.requests)} requests`
                            : 'no data'
                        }
                      />
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center gap-2 pl-10 text-2xs text-ink-3">
        <span>less</span>
        {[0.05, 0.25, 0.45, 0.65, 0.85, 1].map((step) => (
          <span
            key={step}
            className="h-3 w-5 rounded-[2px]"
            style={{ background: sequentialScale(step, dark) }}
            aria-hidden
          />
        ))}
        <span>more</span>
        <span className="ml-auto">peak {compact(max)}</span>
      </div>

      {hovered ? (
        <div className="pointer-events-none absolute right-0 top-0 z-10">
          <TooltipCard
            title={`${DAYS[hovered.day]} ${String(hovered.hour).padStart(2, '0')}:00 UTC`}
            rows={[
              { label: 'Requests', value: compact(hovered.requests) },
              { label: 'Rate', value: `${hovered.rps.toFixed(1)} rps` },
              { label: 'Error rate', value: percent(hovered.error_rate, 2) },
              { label: 'p95', value: ms(hovered.p95_response_time) },
            ]}
          />
        </div>
      ) : null}
    </div>
  )
}
