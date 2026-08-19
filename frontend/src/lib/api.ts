/**
 * Typed API client.
 *
 * One place that knows about base URLs, query-string building, timeouts and
 * error shape — components never touch `fetch` directly.
 */

const RAW_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')
export const API_BASE = RAW_BASE
export const WS_BASE = (import.meta.env.VITE_WS_BASE ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly path: string,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export type QueryValue = string | number | boolean | null | undefined | string[]

function buildQuery(params?: Record<string, QueryValue>): string {
  if (!params) return ''
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      if (value.length) search.set(key, value.join(','))
    } else {
      search.set(key, String(value))
    }
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

export async function apiFetch<T>(
  path: string,
  params?: Record<string, QueryValue>,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const url = `${API_BASE}/api${path}${buildQuery(params)}`
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), init?.timeoutMs ?? 30_000)

  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: { Accept: 'application/json', ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
    })

    if (!response.ok) {
      let detail: unknown
      let message = `${response.status} ${response.statusText}`
      try {
        const body = await response.json()
        detail = body?.error?.detail ?? body?.detail ?? body
        if (typeof detail === 'string') message = detail
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(message, response.status, path, detail)
    }

    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if ((error as Error).name === 'AbortError') {
      throw new ApiError('Request timed out', 408, path)
    }
    throw new ApiError((error as Error).message || 'Network error', 0, path)
  } finally {
    clearTimeout(timeout)
  }
}

export const api = {
  get: <T>(path: string, params?: Record<string, QueryValue>) => apiFetch<T>(path, params),
  patch: <T>(path: string, body: unknown) =>
    apiFetch<T>(path, undefined, { method: 'PATCH', body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    apiFetch<T>(path, undefined, { method: 'PUT', body: JSON.stringify(body) }),
  post: <T>(path: string, body: unknown) =>
    apiFetch<T>(path, undefined, { method: 'POST', body: JSON.stringify(body) }),
  delete: <T>(path: string) => apiFetch<T>(path, undefined, { method: 'DELETE' }),
}

export function wsUrl(path: string, params?: Record<string, QueryValue>): string {
  if (WS_BASE) return `${WS_BASE}${path}${buildQuery(params)}`
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}${buildQuery(params)}`
}
