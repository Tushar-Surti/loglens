/** Sessions — user journeys, conversion and bot share. */

import { useState } from 'react'

import { BarList } from '@/charts/BarList'
import { BotBadge, GroundTruthBadge } from '@/components/Badges'
import { SegmentedControl } from '@/components/Controls'
import { DataTable, type Column } from '@/components/DataTable'
import { PageHeader } from '@/components/PageHeader'
import { Panel, PageTransition, QueryBoundary } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import { useSessions } from '@/hooks/useApi'
import { bytes, clock, cn, compact, duration, ms, number, percent } from '@/lib/format'
import type { SessionRow } from '@/lib/types'

const AUDIENCE = [
  { value: 'all', label: 'Everyone' },
  { value: 'human', label: 'Humans' },
  { value: 'bot', label: 'Bots' },
] as const

export function Sessions() {
  const [audience, setAudience] = useState<(typeof AUDIENCE)[number]['value']>('all')

  const sessions = useSessions({
    limit: 150,
    min_requests: 2,
    bots: audience === 'all' ? undefined : audience === 'bot',
    sort: 'requests',
  })

  const summary = sessions.data?.summary
  const rows = sessions.data?.items ?? []

  const columns: Column<SessionRow>[] = [
    {
      key: 'session',
      header: 'Session',
      sortValue: (row) => row.session_id,
      render: (row) => (
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-xs text-ink">{row.session_id.slice(0, 14)}</span>
          <BotBadge isBot={row.is_bot} />
          <GroundTruthBadge label={(row as any).dominant_attack_label} />
        </div>
      ),
    },
    {
      key: 'user',
      header: 'User',
      hideBelow: 'lg',
      render: (row) => <span className="font-mono text-2xs text-ink-3">{row.user_id ?? 'anonymous'}</span>,
    },
    {
      key: 'source',
      header: 'Source',
      hideBelow: 'md',
      render: (row) => (
        <span className="text-xs text-ink-2">
          {row.country} · <span className="font-mono text-ink-3">{row.ip}</span>
        </span>
      ),
    },
    {
      key: 'requests',
      header: 'Requests',
      align: 'right',
      sortValue: (row) => row.requests,
      render: (row) => <span className="tnum text-xs text-ink">{number(row.requests)}</span>,
    },
    {
      key: 'rpm',
      header: 'Rate',
      align: 'right',
      sortValue: (row) => row.requests_per_minute,
      render: (row) => (
        <span className={cn('tnum text-xs', row.requests_per_minute > 90 ? 'text-warn' : 'text-ink-2')}>
          {number(row.requests_per_minute, 1)}/min
        </span>
      ),
    },
    {
      key: 'duration',
      header: 'Duration',
      align: 'right',
      sortValue: (row) => row.duration_seconds,
      render: (row) => <span className="tnum text-xs text-ink-2">{duration(row.duration_seconds)}</span>,
    },
    {
      key: 'endpoints',
      header: 'Routes',
      align: 'right',
      hideBelow: 'lg',
      sortValue: (row) => row.unique_endpoints,
      render: (row) => <span className="tnum text-xs text-ink-2">{row.unique_endpoints}</span>,
    },
    {
      key: 'errors',
      header: 'Errors',
      align: 'right',
      sortValue: (row) => row.error_count,
      render: (row) => (
        <span className={cn('tnum text-xs', row.error_count > 0 ? 'text-warn' : 'text-ink-3')}>{row.error_count}</span>
      ),
    },
    {
      key: 'converted',
      header: 'Converted',
      align: 'center',
      hideBelow: 'xl',
      sortValue: (row) => (row.converted ? 1 : 0),
      render: (row) =>
        row.converted ? (
          <span className="chip border-good/40 text-good">yes</span>
        ) : (
          <span className="text-2xs text-ink-3">—</span>
        ),
    },
    {
      key: 'started',
      header: 'Started',
      align: 'right',
      hideBelow: 'md',
      sortValue: (row) => row.session_start,
      render: (row) => <span className="tnum text-2xs text-ink-3">{clock(row.session_start, true)}</span>,
    },
  ]

  return (
    <PageTransition>
      <PageHeader
        title="Sessions"
        description="Gap-based sessions built by Spark session windows — real journeys, not fixed buckets."
        actions={
          <SegmentedControl
            options={AUDIENCE.map((item) => ({ value: item.value, label: item.label }))}
            value={audience}
            onChange={setAudience}
            ariaLabel="Audience"
          />
        }
      />

      <StatStrip className="mb-4">
        <StatTile label="Sessions" value={compact(summary?.sessions ?? 0)} />
        <StatTile label="Requests" value={compact(summary?.requests ?? 0)} />
        <StatTile label="Avg duration" value={duration(summary?.avg_duration_seconds ?? 0)} />
        <StatTile label="Requests / session" value={number(summary?.avg_requests_per_session ?? 0, 1)} />
        <StatTile
          label="Conversion"
          value={percent(summary?.conversion_rate ?? 0, 1)}
          tone={(summary?.conversion_rate ?? 0) < 0.02 ? 'warn' : 'good'}
          hint="Sessions that reached checkout or payments"
        />
        <StatTile label="Bot share" value={percent(summary?.bot_share ?? 0, 1)} />
      </StatStrip>

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Entry points" subtitle="where journeys begin">
          <QueryBoundary isLoading={sessions.isLoading} error={sessions.error} height={200}>
            <BarList
              items={(sessions.data?.entry_points ?? []).map((row: any) => ({
                label: row.endpoint ?? 'unknown',
                value: row.sessions,
                display: compact(row.sessions),
              }))}
            />
          </QueryBoundary>
        </Panel>
        <Panel title="Exit points" subtitle="where journeys end">
          <QueryBoundary isLoading={sessions.isLoading} error={sessions.error} height={200}>
            <BarList
              items={(sessions.data?.exit_points ?? []).map((row: any) => ({
                label: row.endpoint ?? 'unknown',
                value: row.sessions,
                display: compact(row.sessions),
              }))}
            />
          </QueryBoundary>
        </Panel>
      </div>

      <Panel title="Session log" subtitle={`${rows.length} sessions`} dense>
        <QueryBoundary isLoading={sessions.isLoading} error={sessions.error} onRetry={sessions.refetch} height={320}>
          <DataTable
            rows={rows}
            columns={columns}
            rowKey={(row) => `${row.session_id}-${row.session_start}`}
            defaultSort={{ key: 'requests', direction: 'desc' }}
            maxHeight={620}
            emptyTitle="No sessions in range"
            emptyMessage="Sessions are emitted once their inactivity gap closes — allow a few minutes after startup."
          />
        </QueryBoundary>
      </Panel>
    </PageTransition>
  )
}
