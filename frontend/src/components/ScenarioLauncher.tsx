/**
 * Incident simulator.
 *
 * Queues a scenario for the running log generator, which produces it into
 * Kafka within ~2 seconds. The anomaly then travels the *real* path — Spark
 * windowing, the detector ensemble, correlation, alerting — so the demo shows
 * the system working rather than a canned animation.
 */

import { AnimatePresence, motion } from 'framer-motion'
import { Check, ChevronDown, Loader2, Zap } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { useInjectScenario, useScenarios } from '@/hooks/useApi'
import { cn, duration } from '@/lib/format'

const INTENSITIES = [
  { value: 0.6, label: 'Mild' },
  { value: 1.0, label: 'Normal' },
  { value: 1.8, label: 'Severe' },
]

export function ScenarioLauncher() {
  const [open, setOpen] = useState(false)
  const [intensity, setIntensity] = useState(1.0)
  const [justSent, setJustSent] = useState<string | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const { data, isLoading } = useScenarios()
  const inject = useInjectScenario()

  useEffect(() => {
    if (!open) return
    const listener = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', listener)
    return () => document.removeEventListener('mousedown', listener)
  }, [open])

  const active = data?.active ?? []
  const catalog = data?.catalog ?? []

  function launch(type: string) {
    inject.mutate(
      { type, intensity },
      {
        onSuccess: () => {
          setJustSent(type)
          window.setTimeout(() => setJustSent(null), 2200)
        },
      },
    )
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={cn('btn btn-sm gap-1.5', active.length > 0 && 'border-warn/50 text-warn')}
        title="Inject a synthetic incident into the live stream"
        aria-expanded={open}
      >
        <Zap className="h-3.5 w-3.5" aria-hidden />
        <span className="hidden sm:inline">Simulate</span>
        {active.length > 0 ? (
          <span className="tnum rounded-xs bg-warn/15 px-1 text-2xs font-semibold">{active.length}</span>
        ) : null}
        <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} aria-hidden />
      </button>

      <AnimatePresence>
        {open ? (
          <motion.div
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
            className="absolute right-0 z-50 mt-1.5 w-[22rem] overflow-hidden rounded-md border border-line-strong bg-surface shadow-xl shadow-black/30"
          >
            <div className="border-b border-line px-3 py-2.5">
              <p className="text-sm font-semibold text-ink">Simulate an incident</p>
              <p className="mt-0.5 text-2xs leading-relaxed text-ink-3">
                Injected into the live Kafka stream. Detection runs through the normal pipeline, so it takes
                one to two windows to surface.
              </p>
            </div>

            <div className="flex items-center gap-2 border-b border-line px-3 py-2">
              <span className="label-caps">Intensity</span>
              <div className="ml-auto flex items-center rounded border border-line p-0.5">
                {INTENSITIES.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setIntensity(option.value)}
                    className={cn(
                      'h-5 rounded-xs px-2 text-2xs font-medium transition-colors',
                      intensity === option.value ? 'bg-raised text-ink' : 'text-ink-3 hover:text-ink-2',
                    )}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>

            {active.length > 0 ? (
              <div className="border-b border-line bg-raised/40 px-3 py-2">
                <p className="label-caps mb-1.5">Running now</p>
                <ul className="space-y-1.5">
                  {active.map((scenario: any) => (
                    <li key={scenario.scenario_id} className="text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-medium text-ink">{scenario.type.replace(/_/g, ' ')}</span>
                        <span className="tnum shrink-0 text-2xs text-ink-3">
                          {duration(Math.max(0, scenario.ends_at * 1000 - Date.now()) / 1000)} left
                        </span>
                      </div>
                      <div className="mt-1 h-1 overflow-hidden rounded-full bg-line">
                        <div
                          className="h-full rounded-full bg-warn transition-[width] duration-1000 ease-linear"
                          style={{ width: `${Math.min(100, (scenario.progress ?? 0) * 100)}%` }}
                        />
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            <div className="max-h-72 overflow-y-auto p-1.5">
              {isLoading ? (
                <p className="px-2 py-4 text-center text-xs text-ink-3">Loading scenarios…</p>
              ) : catalog.length === 0 ? (
                <p className="px-2 py-4 text-center text-xs text-ink-3">
                  The generator has not registered its catalog yet.
                </p>
              ) : (
                catalog.map((entry) => (
                  <button
                    key={entry.type}
                    type="button"
                    disabled={inject.isPending}
                    onClick={() => launch(entry.type)}
                    className="group flex w-full items-start gap-2.5 rounded px-2 py-2 text-left transition-colors hover:bg-raised disabled:opacity-50"
                  >
                    <span className="mt-0.5 text-ink-3 group-hover:text-warn">
                      {justSent === entry.type ? (
                        <Check className="h-3.5 w-3.5 text-good" aria-hidden />
                      ) : inject.isPending ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                      ) : (
                        <Zap className="h-3.5 w-3.5" aria-hidden />
                      )}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-xs font-medium text-ink">{entry.label}</span>
                      <span className="mt-0.5 block text-2xs leading-snug text-ink-3">{entry.description}</span>
                    </span>
                  </button>
                ))
              )}
            </div>

            {inject.isError ? (
              <p className="border-t border-line px-3 py-2 text-2xs text-critical">
                {(inject.error as Error).message}
              </p>
            ) : null}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}
