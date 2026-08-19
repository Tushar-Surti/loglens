/** WebSocket hooks: the metric tick feed and the Kafka-backed log tail. */

import { useCallback, useEffect, useRef, useState } from 'react'

import { wsUrl, type QueryValue } from '@/lib/api'
import type { Anomaly, LiveTick, LogEvent } from '@/lib/types'

type SocketState = 'connecting' | 'open' | 'closed' | 'error'

interface UseSocketOptions {
  enabled?: boolean
  onMessage: (data: any) => void
  maxRetries?: number
}

/**
 * Reconnecting WebSocket with exponential backoff.
 *
 * A dashboard left open overnight will lose its connection; silently dying is
 * the worst outcome, so state is surfaced and retries are capped and jittered.
 */
function useSocket(path: string, params: Record<string, QueryValue>, { enabled = true, onMessage, maxRetries = 8 }: UseSocketOptions) {
  const [state, setState] = useState<SocketState>('closed')
  const socketRef = useRef<WebSocket | null>(null)
  const retriesRef = useRef(0)
  const timerRef = useRef<number>()
  const handlerRef = useRef(onMessage)
  handlerRef.current = onMessage

  const key = JSON.stringify(params)

  const connect = useCallback(() => {
    if (!enabled) return
    setState('connecting')
    let socket: WebSocket
    try {
      socket = new WebSocket(wsUrl(path, JSON.parse(key)))
    } catch {
      setState('error')
      return
    }
    socketRef.current = socket

    socket.onopen = () => {
      retriesRef.current = 0
      setState('open')
    }
    socket.onmessage = (event) => {
      try {
        handlerRef.current(JSON.parse(event.data))
      } catch {
        /* ignore malformed frames */
      }
    }
    socket.onerror = () => setState('error')
    socket.onclose = () => {
      setState('closed')
      if (!enabled || retriesRef.current >= maxRetries) return
      const delay = Math.min(1000 * 2 ** retriesRef.current, 20_000) + Math.random() * 400
      retriesRef.current += 1
      timerRef.current = window.setTimeout(connect, delay)
    }
  }, [enabled, key, maxRetries, path])

  useEffect(() => {
    connect()
    return () => {
      window.clearTimeout(timerRef.current)
      const socket = socketRef.current
      socketRef.current = null
      if (socket && socket.readyState <= WebSocket.OPEN) socket.close()
    }
  }, [connect])

  return { state, retries: retriesRef.current }
}

/** Live platform tick: current window, rolling series and fresh anomalies. */
export function useLiveTicks(enabled = true) {
  const [tick, setTick] = useState<LiveTick | null>(null)
  const [recent, setRecent] = useState<Anomaly[]>([])

  const { state } = useSocket(
    '/ws/live',
    {},
    {
      enabled,
      onMessage: (data) => {
        if (data.type !== 'tick') return
        setTick(data as LiveTick)
        if (data.anomalies?.length) {
          setRecent((previous) => {
            const seen = new Set(previous.map((item) => item.anomaly_id))
            const fresh = (data.anomalies as Anomaly[]).filter((item) => !seen.has(item.anomaly_id))
            return fresh.length ? [...fresh, ...previous].slice(0, 60) : previous
          })
        }
      },
    },
  )

  return { tick, recentAnomalies: recent, connection: state }
}

export interface TailFilters {
  only_errors?: boolean
  min_status?: number
  endpoint?: string
  ip?: string
  service?: string
  search?: string
  rate_limit?: number
}

/** Live log tail with a bounded client-side buffer. */
export function useLogTail(enabled: boolean, filters: TailFilters = {}, bufferSize = 400) {
  const [events, setEvents] = useState<LogEvent[]>([])
  const [throttled, setThrottled] = useState(0)
  const buffer = useRef<LogEvent[]>([])
  const frame = useRef<number>()

  const { state } = useSocket('/ws/logs', filters as Record<string, QueryValue>, {
    enabled,
    onMessage: (data) => {
      if (data.type === 'throttled') {
        setThrottled((count) => count + (data.dropped ?? 0))
        return
      }
      if (data.type !== 'log') return
      // Batch into animation frames: at 40 events/s, one setState per event
      // would re-render the list forty times a second for no visual gain.
      buffer.current = [data.event as LogEvent, ...buffer.current].slice(0, bufferSize)
      if (frame.current) return
      frame.current = window.requestAnimationFrame(() => {
        frame.current = undefined
        setEvents(buffer.current)
      })
    },
  })

  useEffect(() => {
    if (!enabled) {
      buffer.current = []
      setEvents([])
      setThrottled(0)
    }
  }, [enabled])

  useEffect(() => () => { if (frame.current) window.cancelAnimationFrame(frame.current) }, [])

  const clear = useCallback(() => {
    buffer.current = []
    setEvents([])
  }, [])

  return { events, connection: state, throttled, clear }
}

/** Keyboard shortcut helper used by the command palette and page actions. */
export function useHotkey(combo: string, handler: () => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return
    const parts = combo.toLowerCase().split('+')
    const needsMeta = parts.includes('mod')
    const key = parts[parts.length - 1]

    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing = target && ['INPUT', 'TEXTAREA'].includes(target.tagName)
      if (typing && key !== 'escape') return
      if (needsMeta && !(event.metaKey || event.ctrlKey)) return
      if (!needsMeta && (event.metaKey || event.ctrlKey)) return
      if (event.key.toLowerCase() !== key) return
      event.preventDefault()
      handler()
    }
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [combo, enabled, handler])
}
