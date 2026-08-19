/**
 * ⌘K command palette: navigation, range switching, theme and demo actions.
 *
 * Hand-rolled rather than a dependency — it needs to reach app state (range,
 * theme, scenario injection), and the whole surface is ~150 lines.
 */

import { AnimatePresence, motion } from 'framer-motion'
import {
  Activity,
  BarChart3,
  Globe2,
  Search as SearchIcon,
  Settings2,
  ShieldAlert,
  Siren,
  Sun,
  Terminal,
  Timer,
  Zap,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import { useInjectScenario, useScenarios } from '@/hooks/useApi'
import { cn } from '@/lib/format'
import { RANGE_PRESETS, useUi, type RangeValue } from '@/lib/store'

interface Command {
  id: string
  label: string
  hint?: string
  group: string
  icon: ReactNode
  run: () => void
  keywords?: string
}

export function CommandPalette() {
  const { commandOpen, setCommandOpen, setRange, toggleTheme } = useUi()
  const navigate = useNavigate()
  const [term, setTerm] = useState('')
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const { data: scenarios } = useScenarios()
  const inject = useInjectScenario()

  const commands = useMemo<Command[]>(() => {
    const go = (path: string) => () => {
      navigate(path)
      setCommandOpen(false)
    }
    const navigation: Command[] = [
      { id: 'nav-overview', label: 'Overview', group: 'Navigate', icon: <BarChart3 className="h-4 w-4" />, run: go('/') },
      { id: 'nav-traffic', label: 'Traffic', group: 'Navigate', icon: <Activity className="h-4 w-4" />, run: go('/traffic') },
      { id: 'nav-endpoints', label: 'Endpoints', group: 'Navigate', icon: <Timer className="h-4 w-4" />, run: go('/endpoints') },
      { id: 'nav-logs', label: 'Log Explorer', group: 'Navigate', icon: <Terminal className="h-4 w-4" />, run: go('/logs'), keywords: 'search query logs' },
      { id: 'nav-anomalies', label: 'Anomalies', group: 'Navigate', icon: <ShieldAlert className="h-4 w-4" />, run: go('/anomalies') },
      { id: 'nav-incidents', label: 'Incidents', group: 'Navigate', icon: <Siren className="h-4 w-4" />, run: go('/incidents') },
      { id: 'nav-geo', label: 'Geography', group: 'Navigate', icon: <Globe2 className="h-4 w-4" />, run: go('/geography') },
      { id: 'nav-health', label: 'System Health', group: 'Navigate', icon: <Activity className="h-4 w-4" />, run: go('/health'), keywords: 'pipeline kafka spark' },
      { id: 'nav-settings', label: 'Settings', group: 'Navigate', icon: <Settings2 className="h-4 w-4" />, run: go('/settings'), keywords: 'thresholds rules' },
    ]

    const ranges: Command[] = RANGE_PRESETS.map((preset) => ({
      id: `range-${preset.value}`,
      label: preset.full,
      hint: preset.label,
      group: 'Time range',
      icon: <Timer className="h-4 w-4" />,
      run: () => {
        setRange(preset.value as RangeValue)
        setCommandOpen(false)
      },
    }))

    const injections: Command[] = (scenarios?.catalog ?? []).map((entry) => ({
      id: `scenario-${entry.type}`,
      label: `Inject: ${entry.label}`,
      hint: 'demo',
      group: 'Simulate incident',
      icon: <Zap className="h-4 w-4" />,
      keywords: entry.description,
      run: () => {
        inject.mutate({ type: entry.type })
        setCommandOpen(false)
      },
    }))

    return [
      ...navigation,
      ...ranges,
      ...injections,
      {
        id: 'toggle-theme',
        label: 'Toggle light / dark theme',
        group: 'Preferences',
        icon: <Sun className="h-4 w-4" />,
        run: () => {
          toggleTheme()
          setCommandOpen(false)
        },
      },
    ]
  }, [inject, navigate, scenarios, setCommandOpen, setRange, toggleTheme])

  const filtered = useMemo(() => {
    const needle = term.trim().toLowerCase()
    if (!needle) return commands
    return commands.filter((command) =>
      `${command.label} ${command.group} ${command.keywords ?? ''}`.toLowerCase().includes(needle),
    )
  }, [commands, term])

  useEffect(() => {
    setCursor(0)
  }, [term])

  useEffect(() => {
    if (commandOpen) {
      setTerm('')
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [commandOpen])

  useEffect(() => {
    if (!commandOpen) return
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCommandOpen(false)
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setCursor((index) => Math.min(index + 1, filtered.length - 1))
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setCursor((index) => Math.max(index - 1, 0))
      }
      if (event.key === 'Enter') {
        event.preventDefault()
        filtered[cursor]?.run()
      }
    }
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [commandOpen, cursor, filtered, setCommandOpen])

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [cursor])

  let lastGroup = ''

  return (
    <AnimatePresence>
      {commandOpen ? (
        <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh]" role="dialog" aria-modal="true">
          <motion.div
            className="absolute inset-0 bg-black/50 backdrop-blur-[3px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.14 }}
            onClick={() => setCommandOpen(false)}
          />
          <motion.div
            className="relative w-full max-w-lg overflow-hidden rounded-md border border-line-strong bg-surface shadow-2xl shadow-black/40"
            initial={{ opacity: 0, y: -8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="flex items-center gap-2 border-b border-line px-3">
              <SearchIcon className="h-4 w-4 shrink-0 text-ink-3" aria-hidden />
              <input
                ref={inputRef}
                value={term}
                onChange={(event) => setTerm(event.target.value)}
                placeholder="Jump to a view, change the range, simulate an incident…"
                className="h-11 w-full bg-transparent text-sm text-ink outline-none placeholder:text-ink-3"
                spellCheck={false}
              />
              <kbd className="hidden shrink-0 rounded border border-line px-1.5 py-0.5 font-mono text-2xs text-ink-3 sm:block">
                esc
              </kbd>
            </div>

            <div ref={listRef} className="max-h-[52vh] overflow-y-auto p-1.5">
              {filtered.length === 0 ? (
                <p className="px-3 py-8 text-center text-sm text-ink-3">No matching command</p>
              ) : (
                filtered.map((command, index) => {
                  const showGroup = command.group !== lastGroup
                  lastGroup = command.group
                  return (
                    <div key={command.id}>
                      {showGroup ? <p className="label-caps px-2 pb-1 pt-2">{command.group}</p> : null}
                      <button
                        type="button"
                        data-active={index === cursor}
                        onMouseEnter={() => setCursor(index)}
                        onClick={command.run}
                        className={cn(
                          'flex w-full items-center gap-2.5 rounded px-2 py-1.5 text-left text-sm transition-colors',
                          index === cursor ? 'bg-raised text-ink' : 'text-ink-2',
                        )}
                      >
                        <span className="text-ink-3">{command.icon}</span>
                        <span className="min-w-0 flex-1 truncate">{command.label}</span>
                        {command.hint ? <span className="text-2xs text-ink-3">{command.hint}</span> : null}
                      </button>
                    </div>
                  )
                })
              )}
            </div>
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>
  )
}
