/**
 * Application shell: fixed sidebar, sticky toolbar, scrolling content column.
 *
 * The layout is deliberately conventional — an operator should be able to find
 * things without learning a novel navigation model. Everything expressive
 * happens inside the panels.
 */

import Lenis from 'lenis'
import {
  Activity,
  BarChart3,
  Bell,
  Globe2,
  Keyboard,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings2,
  ShieldAlert,
  Siren,
  Sun,
  Terminal,
  Timer,
  Users,
} from 'lucide-react'
import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { CommandPalette } from '@/components/CommandPalette'
import { LiveToggle, TimeRangePicker } from '@/components/Controls'
import { HealthPill } from '@/components/Badges'
import { ScenarioLauncher } from '@/components/ScenarioLauncher'
import { useLiveTicks, useHotkey } from '@/hooks/useLive'
import { useOverview, useSystemHealth } from '@/hooks/useApi'
import { cn, compact } from '@/lib/format'
import { useUi } from '@/lib/store'

interface NavItem {
  to: string
  label: string
  icon: ReactNode
  badge?: number
}

const SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: 'Monitor',
    items: [
      { to: '/', label: 'Overview', icon: <BarChart3 className="h-4 w-4" /> },
      { to: '/traffic', label: 'Traffic', icon: <Activity className="h-4 w-4" /> },
      { to: '/endpoints', label: 'Endpoints', icon: <Timer className="h-4 w-4" /> },
      { to: '/sessions', label: 'Sessions', icon: <Users className="h-4 w-4" /> },
    ],
  },
  {
    title: 'Investigate',
    items: [
      { to: '/logs', label: 'Log Explorer', icon: <Terminal className="h-4 w-4" /> },
      { to: '/geography', label: 'Geography', icon: <Globe2 className="h-4 w-4" /> },
    ],
  },
  {
    title: 'Detect',
    items: [
      { to: '/anomalies', label: 'Anomalies', icon: <ShieldAlert className="h-4 w-4" /> },
      { to: '/incidents', label: 'Incidents', icon: <Siren className="h-4 w-4" /> },
      { to: '/alerts', label: 'Alerts', icon: <Bell className="h-4 w-4" /> },
    ],
  },
  {
    title: 'Platform',
    items: [
      { to: '/health', label: 'System Health', icon: <Activity className="h-4 w-4" /> },
      { to: '/settings', label: 'Settings', icon: <Settings2 className="h-4 w-4" /> },
    ],
  },
]

function Brand({ collapsed }: { collapsed: boolean }) {
  return (
    <div className={cn('flex h-14 shrink-0 items-center gap-2.5 border-b border-line px-4', collapsed && 'px-0 justify-center')}>
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden className="shrink-0">
        <rect x="0.75" y="0.75" width="18.5" height="18.5" rx="4.25" stroke="rgb(var(--accent))" strokeWidth="1.5" />
        <path d="M4 13.2L7.6 9.1L10.6 11.7L16 5.4" stroke="rgb(var(--accent))" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="7.6" cy="9.1" r="1.5" fill="rgb(var(--surface))" stroke="rgb(var(--accent))" strokeWidth="1.4" />
      </svg>
      {!collapsed ? (
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-tight text-ink">LogLens</p>
          <p className="truncate text-2xs text-ink-3">Observability &amp; detection</p>
        </div>
      ) : null}
    </div>
  )
}

function Sidebar() {
  const { sidebarCollapsed, toggleSidebar } = useUi()
  const { data: overview } = useOverview()
  const { tick } = useLiveTicks(true)

  const openIncidents = overview?.incidents.open ?? 0
  const criticalAnomalies =
    (overview?.anomalies.by_severity.critical ?? 0) + (overview?.anomalies.by_severity.high ?? 0)

  return (
    <aside
      className={cn(
        'hidden shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-200 ease-out lg:flex',
        sidebarCollapsed ? 'w-[60px]' : 'w-[218px]',
      )}
    >
      <Brand collapsed={sidebarCollapsed} />

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 py-3" aria-label="Primary">
        {SECTIONS.map((section) => (
          <div key={section.title} className="mb-4">
            {!sidebarCollapsed ? <p className="label-caps px-2 pb-1.5">{section.title}</p> : null}
            <ul className="space-y-0.5">
              {section.items.map((item) => {
                const badge =
                  item.to === '/incidents' ? openIncidents : item.to === '/anomalies' ? criticalAnomalies : 0
                return (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.to === '/'}
                      title={sidebarCollapsed ? item.label : undefined}
                      className={({ isActive }) =>
                        cn(
                          'group relative flex h-8 items-center gap-2.5 rounded px-2 text-sm transition-colors',
                          sidebarCollapsed && 'justify-center px-0',
                          isActive
                            ? 'bg-raised font-medium text-ink'
                            : 'text-ink-2 hover:bg-raised/60 hover:text-ink',
                        )
                      }
                    >
                      {({ isActive }) => (
                        <>
                          {isActive ? (
                            <span className="absolute left-0 top-1.5 h-5 w-[2px] rounded-full bg-accent" aria-hidden />
                          ) : null}
                          <span className="shrink-0 text-ink-3 group-hover:text-ink-2">{item.icon}</span>
                          {!sidebarCollapsed ? (
                            <>
                              <span className="min-w-0 flex-1 truncate">{item.label}</span>
                              {badge > 0 ? (
                                <span className="tnum rounded-xs bg-critical/15 px-1.5 text-2xs font-semibold text-critical">
                                  {badge > 99 ? '99+' : badge}
                                </span>
                              ) : null}
                            </>
                          ) : null}
                        </>
                      )}
                    </NavLink>
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="shrink-0 border-t border-line p-2">
        {!sidebarCollapsed ? (
          <div className="mb-2 rounded border border-line bg-raised/40 px-2.5 py-2">
            <div className="flex items-center justify-between">
              <span className="label-caps">Ingest</span>
              <span className="tnum text-xs font-semibold text-ink">
                {compact(tick?.current?.rps ?? 0)}
                <span className="ml-0.5 text-2xs font-normal text-ink-3">rps</span>
              </span>
            </div>
            <div className="mt-1.5 flex items-center gap-1.5 text-2xs text-ink-3">
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  tick ? 'bg-good' : 'bg-ink-3',
                )}
                aria-hidden
              />
              {tick ? 'stream connected' : 'connecting…'}
            </div>
          </div>
        ) : null}
        <button
          type="button"
          onClick={toggleSidebar}
          className="btn btn-ghost btn-sm w-full justify-center"
          aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </button>
      </div>
    </aside>
  )
}

function MobileNav() {
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-line bg-surface px-2 py-1.5 lg:hidden">
      {SECTIONS.flatMap((section) => section.items).map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/'}
          className={({ isActive }) =>
            cn(
              'flex h-7 shrink-0 items-center gap-1.5 rounded px-2 text-xs transition-colors',
              isActive ? 'bg-raised font-medium text-ink' : 'text-ink-2',
            )
          }
        >
          {item.icon}
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}

function TopBar() {
  const { theme, toggleTheme, setCommandOpen } = useUi()
  const { data: health } = useSystemHealth()

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-line bg-surface/85 px-3 backdrop-blur-md sm:px-4">
      <button
        type="button"
        className="btn btn-ghost btn-sm lg:hidden"
        onClick={() => setCommandOpen(true)}
        aria-label="Open menu"
      >
        <Menu className="h-4 w-4" />
      </button>

      <button
        type="button"
        onClick={() => setCommandOpen(true)}
        className="group hidden h-8 min-w-[220px] items-center gap-2 rounded border border-line bg-canvas px-2.5 text-left text-sm text-ink-3 transition-colors hover:border-line-strong md:flex"
      >
        <Keyboard className="h-3.5 w-3.5" aria-hidden />
        <span className="flex-1 truncate">Search or jump to…</span>
        <kbd className="rounded border border-line px-1 py-px font-mono text-2xs">⌘K</kbd>
      </button>

      <div className="ml-auto flex items-center gap-2">
        <HealthPill status={health?.status ?? 'unknown'} />
        <TimeRangePicker />
        <LiveToggle />
        <ScenarioLauncher />
        <button
          type="button"
          onClick={toggleTheme}
          className="btn btn-ghost btn-sm"
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>
    </header>
  )
}

export function AppShell() {
  const { setCommandOpen, commandOpen } = useUi()
  const location = useLocation()
  const scrollRef = useRef<HTMLDivElement>(null)

  useHotkey('mod+k', () => setCommandOpen(!commandOpen))
  useHotkey('escape', () => setCommandOpen(false), commandOpen)

  // Lenis smooths the long scrolling views (log explorer, incident timelines)
  // without hijacking the wheel inside virtualised tables.
  useEffect(() => {
    const wrapper = scrollRef.current
    if (!wrapper) return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

    const lenis = new Lenis({
      wrapper,
      content: wrapper.firstElementChild as HTMLElement,
      duration: 0.85,
      easing: (t: number) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smoothWheel: true,
    })
    let frame = 0
    const raf = (time: number) => {
      lenis.raf(time)
      frame = requestAnimationFrame(raf)
    }
    frame = requestAnimationFrame(raf)
    return () => {
      cancelAnimationFrame(frame)
      lenis.destroy()
    }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 })
  }, [location.pathname])

  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <MobileNav />
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <main className="mx-auto w-full max-w-[1680px] px-3 py-4 sm:px-5 sm:py-5">
            <Outlet />
          </main>
        </div>
      </div>
      <CommandPalette />
    </div>
  )
}
