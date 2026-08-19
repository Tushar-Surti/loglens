/**
 * Log Explorer — query, histogram, virtualised results and a live tail.
 *
 * The query bar speaks a small field:value language (`status>=500`,
 * `endpoint:/api/v1/*`, `rt>800`, bare words are full-text). Unparseable input
 * degrades to a text search rather than erroring, because people type while
 * they think.
 */

import { AnimatePresence, motion } from 'framer-motion'
import { useVirtualizer } from '@tanstack/react-virtual'
import { AlertCircle, Radio, RotateCcw, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { StackedStatusChart } from '@/charts/StackedStatusChart'
import { BotBadge, GroundTruthBadge, MethodBadge, StatusCode } from '@/components/Badges'
import { ConnectionBadge, SearchInput, SegmentedControl, useDebounced } from '@/components/Controls'
import { DefinitionList, Drawer } from '@/components/Drawer'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, ErrorState, Panel, PageTransition, SkeletonRows } from '@/components/Panel'
import { useLogDetail, useLogFacets, useLogHistogram, useLogs } from '@/hooks/useApi'
import { useLogTail } from '@/hooks/useLive'
import { bytes, clock, cn, compact, dateTime, ms, number, percent, relativeTime } from '@/lib/format'
import type { LogEvent } from '@/lib/types'

const QUERY_EXAMPLES = [
  { query: 'status>=500', label: 'Server errors' },
  { query: 'endpoint:/api/v1/* rt>800', label: 'Slow API calls' },
  { query: 'status:401 endpoint:/api/v1/auth/login', label: 'Failed logins' },
  { query: 'is_bot:true', label: 'Bot traffic' },
  { query: 'country:RU', label: 'By origin' },
  { query: 'label:ddos_burst', label: 'Injected attacks' },
]

const PAGE_SIZE = 200

export function LogExplorer() {
  const [query, setQuery] = useState('')
  const [onlyErrors, setOnlyErrors] = useState(false)
  const [mode, setMode] = useState<'search' | 'tail'>('search')
  const [selected, setSelected] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const debouncedQuery = useDebounced(query, 400)

  const params = useMemo(
    () => ({ q: debouncedQuery || undefined, only_errors: onlyErrors || undefined, limit: PAGE_SIZE, offset }),
    [debouncedQuery, onlyErrors, offset],
  )

  const logs = useLogs(params, mode === 'search')
  const histogram = useLogHistogram({ q: debouncedQuery || undefined, only_errors: onlyErrors || undefined })
  const facets = useLogFacets({ q: debouncedQuery || undefined, only_errors: onlyErrors || undefined })
  const tail = useLogTail(mode === 'tail', {
    only_errors: onlyErrors,
    search: debouncedQuery && !debouncedQuery.includes(':') ? debouncedQuery : undefined,
  })

  useEffect(() => {
    setOffset(0)
  }, [debouncedQuery, onlyErrors])

  const rows = mode === 'tail' ? tail.events : (logs.data?.items ?? [])
  const total = logs.data?.total

  return (
    <PageTransition>
      <PageHeader
        title="Log Explorer"
        description="Search every request the pipeline has stored, or tail the live stream straight off Kafka."
        actions={
          <SegmentedControl
            options={[
              { value: 'search', label: 'Search' },
              { value: 'tail', label: (<span className="flex items-center gap-1"><Radio className="h-3 w-3" />Live tail</span>) as any },
            ]}
            value={mode}
            onChange={(value) => setMode(value as 'search' | 'tail')}
            ariaLabel="Explorer mode"
          />
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <SearchInput
          value={query}
          onChange={setQuery}
          placeholder="status>=500 endpoint:/api/v1/* rt>800"
          className="min-w-[280px] flex-1"
          mono
        />
        <button
          type="button"
          onClick={() => setOnlyErrors((value) => !value)}
          className={cn('btn btn-sm', onlyErrors && 'border-critical/45 text-critical')}
          aria-pressed={onlyErrors}
        >
          <AlertCircle className="h-3.5 w-3.5" aria-hidden />
          Errors only
        </button>
        {mode === 'tail' ? (
          <>
            <ConnectionBadge state={tail.connection} />
            <button type="button" className="btn btn-sm" onClick={tail.clear}>
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
              Clear
            </button>
          </>
        ) : (
          <button type="button" className="btn btn-sm" onClick={() => logs.refetch()}>
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            Refresh
          </button>
        )}
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <span className="label-caps mr-1">Try</span>
        {QUERY_EXAMPLES.map((example) => (
          <button
            key={example.query}
            type="button"
            onClick={() => setQuery(example.query)}
            className="chip transition-colors hover:border-line-strong hover:text-ink"
            title={example.query}
          >
            {example.label}
          </button>
        ))}
      </div>

      {mode === 'search' ? (
        <Panel
          title="Matches over time"
          subtitle={total !== null && total !== undefined ? `${number(total)} matching requests` : 'live count'}
          className="mb-4"
        >
          {histogram.isLoading ? (
            <SkeletonRows rows={4} />
          ) : histogram.error ? (
            <ErrorState error={histogram.error} onRetry={histogram.refetch} compact />
          ) : !histogram.data?.points.length ? (
            <EmptyState title="No matches in this range" message="Try a broader query or a longer time range." />
          ) : (
            <StackedStatusChart
              data={histogram.data.points}
              classes={histogram.data.classes}
              bucketSeconds={histogram.data.range.bucket_seconds}
              height={140}
              variant="bars"
            />
          )}
        </Panel>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_236px]">
        <Panel
          title={mode === 'tail' ? 'Live stream' : 'Results'}
          subtitle={
            mode === 'tail'
              ? `${rows.length} buffered${tail.throttled ? ` · ${tail.throttled} dropped by rate limit` : ''}`
              : `${rows.length} shown${total ? ` of ${number(total)}` : ''}`
          }
          dense
          actions={
            mode === 'search' ? (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={offset === 0}
                  onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  disabled={!logs.data?.has_more}
                  onClick={() => setOffset((value) => value + PAGE_SIZE)}
                >
                  Next
                </button>
              </div>
            ) : null
          }
        >
          {logs.isLoading && mode === 'search' ? (
            <SkeletonRows rows={12} className="p-4" />
          ) : logs.error && mode === 'search' ? (
            <ErrorState error={logs.error} onRetry={logs.refetch} />
          ) : rows.length === 0 ? (
            <EmptyState
              title={mode === 'tail' ? 'Waiting for events…' : 'No requests matched'}
              message={
                mode === 'tail'
                  ? 'The tail shows events as they arrive on the enriched Kafka topic.'
                  : 'Check the query syntax, or widen the time range.'
              }
            />
          ) : (
            <LogList rows={rows} onSelect={setSelected} animate={mode === 'tail'} />
          )}
        </Panel>

        <div className="space-y-4">
          <Panel title="Facets" dense bodyClassName="p-3">
            {facets.isLoading ? (
              <SkeletonRows rows={6} />
            ) : (
              <div className="space-y-4">
                {(['status_class', 'method', 'service', 'country'] as const).map((dimension) => {
                  const values = facets.data?.facets?.[dimension] ?? []
                  if (!values.length) return null
                  const max = Math.max(...values.map((entry) => entry.count), 1)
                  return (
                    <div key={dimension}>
                      <p className="label-caps mb-1.5">{dimension.replace('_', ' ')}</p>
                      <ul className="space-y-1">
                        {values.slice(0, 6).map((entry) => (
                          <li key={String(entry.value)}>
                            <button
                              type="button"
                              onClick={() =>
                                setQuery((current) =>
                                  `${current} ${dimension === 'status_class' ? 'status_class' : dimension}:${entry.value}`.trim(),
                                )
                              }
                              className="group relative flex w-full items-center justify-between gap-2 overflow-hidden rounded-sm px-1.5 py-1 text-left"
                            >
                              <span
                                className="absolute inset-y-0 left-0 rounded-sm bg-accent/10"
                                style={{ width: `${(entry.count / max) * 100}%` }}
                                aria-hidden
                              />
                              <span className="relative truncate text-xs text-ink-2 group-hover:text-ink">
                                {String(entry.value)}
                              </span>
                              <span className="relative tnum shrink-0 text-2xs text-ink-3">{compact(entry.count)}</span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )
                })}
              </div>
            )}
          </Panel>

          <Panel title="Query syntax" dense bodyClassName="p-3">
            <dl className="space-y-2 text-2xs">
              {[
                ['field:value', 'exact match'],
                ['field>=n', 'numeric comparison'],
                ['path:/api/*', 'glob'],
                ['-status:200', 'negation'],
                ['word', 'full text'],
              ].map(([syntax, meaning]) => (
                <div key={syntax} className="flex items-baseline justify-between gap-2">
                  <dt className="font-mono text-ink-2">{syntax}</dt>
                  <dd className="text-ink-3">{meaning}</dd>
                </div>
              ))}
            </dl>
          </Panel>
        </div>
      </div>

      <LogDrawer eventId={selected} onClose={() => setSelected(null)} />
    </PageTransition>
  )
}

/** Virtualised so a 200-row page (or a live tail) never costs 200 renders. */
function LogList({
  rows,
  onSelect,
  animate,
}: {
  rows: LogEvent[]
  onSelect: (id: string) => void
  animate?: boolean
}) {
  const parentRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 34,
    overscan: 12,
  })

  return (
    <div ref={parentRef} className="max-h-[600px] overflow-auto" data-lenis-prevent>
      <div className="sticky top-0 z-10 flex items-center gap-3 border-b border-line bg-surface px-3 py-1.5 text-2xs uppercase tracking-wide text-ink-3">
        <span className="w-16 shrink-0">Time</span>
        <span className="w-11 shrink-0">Status</span>
        <span className="w-14 shrink-0">Method</span>
        <span className="min-w-0 flex-1">Path</span>
        <span className="hidden w-20 shrink-0 text-right sm:block">Latency</span>
        <span className="hidden w-28 shrink-0 text-right lg:block">Source</span>
      </div>

      <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
        <AnimatePresence initial={false}>
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const event = rows[virtualRow.index]
            const isNew = animate && virtualRow.index === 0
            return (
              <motion.button
                key={event.event_id}
                type="button"
                initial={isNew ? { opacity: 0, backgroundColor: 'rgb(var(--accent) / 0.12)' } : false}
                animate={{ opacity: 1, backgroundColor: 'rgba(0,0,0,0)' }}
                transition={{ duration: 0.5 }}
                onClick={() => onSelect(event.event_id)}
                className="absolute left-0 flex w-full items-center gap-3 border-b border-line/60 px-3 text-left transition-colors hover:bg-raised/70"
                style={{ height: virtualRow.size, transform: `translateY(${virtualRow.start}px)` }}
              >
                <span className="tnum w-16 shrink-0 font-mono text-2xs text-ink-3">
                  {clock(event.timestamp, true)}
                </span>
                <span className="w-11 shrink-0">
                  <StatusCode code={event.status} />
                </span>
                <span className="w-14 shrink-0">
                  <MethodBadge method={event.method} />
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-2">
                  {event.path}
                  {event.attack_label ? <span className="ml-2"><GroundTruthBadge label={event.attack_label} /></span> : null}
                </span>
                <span
                  className={cn(
                    'tnum hidden w-20 shrink-0 text-right text-xs sm:block',
                    event.response_time_ms > 1000 ? 'text-warn' : 'text-ink-2',
                  )}
                >
                  {ms(event.response_time_ms)}
                </span>
                <span className="hidden w-28 shrink-0 truncate text-right font-mono text-2xs text-ink-3 lg:block">
                  {event.country} {event.ip}
                </span>
              </motion.button>
            )
          })}
        </AnimatePresence>
      </div>
    </div>
  )
}

function LogDrawer({ eventId, onClose }: { eventId: string | null; onClose: () => void }) {
  const detail = useLogDetail(eventId)
  const event = detail.data?.event

  return (
    <Drawer
      open={Boolean(eventId)}
      onClose={onClose}
      title={event ? `${event.method} ${event.path}` : 'Request'}
      subtitle={event ? `${dateTime(event.timestamp)} · ${relativeTime(event.timestamp)}` : undefined}
      width="max-w-2xl"
    >
      {detail.isLoading ? (
        <SkeletonRows rows={10} className="p-4" />
      ) : !event ? (
        <EmptyState title="Request not found" message="It may have aged out of the retention window." />
      ) : (
        <div className="space-y-5 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusCode code={event.status} />
            <MethodBadge method={event.method} />
            <BotBadge isBot={event.is_bot} />
            <GroundTruthBadge label={event.attack_label} />
            <span className="chip">{ms(event.response_time_ms)}</span>
            <span className="chip">{bytes(event.bytes_sent)}</span>
            <span className="chip">{event.cache_status}</span>
          </div>

          <DefinitionList
            items={[
              { label: 'Event ID', value: event.event_id, mono: true },
              { label: 'Endpoint', value: event.endpoint, mono: true },
              { label: 'Source IP', value: event.ip, mono: true },
              { label: 'Location', value: `${event.city ?? '—'}, ${event.country_name ?? event.country ?? '—'}` },
              { label: 'Network', value: `${event.asn ?? '—'} · ${event.org ?? '—'}` },
              { label: 'Service', value: `${event.service ?? '—'} @ ${event.host ?? '—'}` },
              { label: 'Region', value: event.region ?? '—' },
              { label: 'Session', value: event.session_id ?? '—', mono: true },
              { label: 'User', value: event.user_id ?? 'anonymous', mono: true },
              { label: 'Device', value: `${event.device ?? '—'} · ${event.browser ?? '—'} · ${event.os ?? '—'}` },
              { label: 'Upstream time', value: ms(event.upstream_time_ms ?? 0) },
              { label: 'Referrer', value: event.referrer || '—', span: true },
              { label: 'User agent', value: event.user_agent, mono: true, span: true },
            ]}
          />

          {detail.data?.session_events?.length ? (
            <div>
              <p className="label-caps mb-2">Session trail ({detail.data.session_events.length} requests)</p>
              <ol className="relative space-y-1 border-l border-line pl-4">
                {detail.data.session_events.map((step) => (
                  <li key={step.event_id} className="relative">
                    <span
                      className={cn(
                        'absolute -left-[21px] top-2 h-1.5 w-1.5 rounded-full',
                        step.event_id === event.event_id ? 'bg-accent ring-2 ring-accent/30' : 'bg-line-strong',
                      )}
                      aria-hidden
                    />
                    <div
                      className={cn(
                        'flex items-center justify-between gap-3 rounded px-2 py-1',
                        step.event_id === event.event_id && 'bg-accent/8',
                      )}
                    >
                      <span className="truncate font-mono text-2xs text-ink-2">{step.path}</span>
                      <span className="flex shrink-0 items-center gap-2">
                        <StatusCode code={step.status} />
                        <span className="tnum text-2xs text-ink-3">{ms(step.response_time_ms)}</span>
                        <span className="tnum text-2xs text-ink-3">{clock(step.timestamp, true)}</span>
                      </span>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}

          {detail.data?.ip_activity?.length ? (
            <div>
              <p className="label-caps mb-2">Recent activity from {event.ip}</p>
              <ul className="divide-y divide-line rounded border border-line text-2xs">
                {detail.data.ip_activity.slice(0, 8).map((window: any) => (
                  <li key={window.window_start} className="flex items-center justify-between gap-3 px-3 py-1.5">
                    <span className="text-ink-3">{clock(window.window_start)}</span>
                    <span className="flex items-center gap-3 text-ink-2">
                      <span className="tnum">{compact(window.requests)} req</span>
                      <span className="tnum">{percent(window.error_ratio, 1)} err</span>
                      <span className="tnum">{window.unique_paths} paths</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </Drawer>
  )
}
