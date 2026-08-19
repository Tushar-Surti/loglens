/** Global UI state: theme, time range and live-streaming preferences. */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export const RANGE_PRESETS = [
  { value: '15m', label: '15m', full: 'Last 15 minutes' },
  { value: '1h', label: '1h', full: 'Last hour' },
  { value: '6h', label: '6h', full: 'Last 6 hours' },
  { value: '24h', label: '24h', full: 'Last 24 hours' },
  { value: '7d', label: '7d', full: 'Last 7 days' },
] as const

export type RangeValue = (typeof RANGE_PRESETS)[number]['value']

interface UiState {
  theme: 'dark' | 'light'
  range: RangeValue
  live: boolean
  sidebarCollapsed: boolean
  commandOpen: boolean
  setTheme: (theme: 'dark' | 'light') => void
  toggleTheme: () => void
  setRange: (range: RangeValue) => void
  setLive: (live: boolean) => void
  toggleSidebar: () => void
  setCommandOpen: (open: boolean) => void
}

function applyTheme(theme: 'dark' | 'light') {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

export const useUi = create<UiState>()(
  persist(
    (set, get) => ({
      theme: 'dark',
      range: '1h',
      live: true,
      sidebarCollapsed: false,
      commandOpen: false,
      setTheme: (theme) => {
        applyTheme(theme)
        set({ theme })
      },
      toggleTheme: () => get().setTheme(get().theme === 'dark' ? 'light' : 'dark'),
      setRange: (range) => set({ range }),
      setLive: (live) => set({ live }),
      toggleSidebar: () => set({ sidebarCollapsed: !get().sidebarCollapsed }),
      setCommandOpen: (commandOpen) => set({ commandOpen }),
    }),
    {
      name: 'loglens-ui',
      partialize: (state) => ({
        theme: state.theme,
        range: state.range,
        live: state.live,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
      onRehydrateStorage: () => (state) => {
        if (state) applyTheme(state.theme)
      },
    },
  ),
)

/** Poll cadence per range: short windows refresh fast, long ones rarely. */
export function refetchInterval(range: RangeValue, live: boolean): number | false {
  if (!live) return false
  switch (range) {
    case '15m':
      return 5_000
    case '1h':
      return 10_000
    case '6h':
      return 30_000
    case '24h':
      return 60_000
    default:
      return 120_000
  }
}

// Persisted state is restored before React mounts; keep the DOM class in sync
// for the very first paint too.
if (typeof document !== 'undefined') {
  applyTheme(useUi.getState().theme)
}
