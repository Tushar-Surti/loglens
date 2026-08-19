/** Endpoint performance table with a drill-down drawer. */

import { useMemo, useState } from 'react'

import { Sparkline } from '@/charts/Sparkline'
import { TimeSeriesChart } from '@/charts/TimeSeriesChart'
import { useChartTheme } from '@/charts/palette'
import { MethodBadge, SeverityBadge, StatusCode } from '@/components/Badges'
import { SearchInput, SegmentedControl, useDebounced } from '@/components/Controls'
import { DataTable, type Column } from '@/components/DataTable'
import { DefinitionList, Drawer } from '@/components/Drawer'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState, Panel, PageTransition, QueryBoundary, SkeletonRows } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import { useEndpointDetail, useEndpoints, useServices } from '@/hooks/useApi'
import { bytes, clock, cn, compact, ms, percent } from '@/lib/format'
import type { EndpointRow } from '@/lib/types'

const SORTS = [
  { value: 'requests', label: 'Traffic' },
  { value: 'p95', label: 'Slowest' },
  { value: 'error_rate', label: 'Failing' },
  { value: 'apdex', label: 'Worst Apdex' },
] as const

export function Endpoints() {
  const theme = useChartTheme()
  const [sort, setSort] = useState<(typeof SORTS)[number]['value']>('requests')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<EndpointRow | null>(null)
  const debounced = useDebounced(search)

  const endpoints = useEndpoints({ limit: 100, sort, search: debounced || undefined })
  const services = useServices()

  const rows = endpoints.data?.items ?? []
  const totals = useMemo(() => {
    const requests = rows.reduce((sum, row) => sum + row.requests, 0)
    const errors = rows.reduce((sum, row) => sum + row.errors_4xx + row.errors_5xx, 0)
    const slowest = rows.reduce<EndpointRow | null>(
      (worst, row) => (!worst || row.p95_response_time > worst.p95_response_time ? row : worst),
      null,
    )
    const worstApdex = rows.reduce<EndpointRow | null>(
      (worst, row) => (!worst || row.apdex < worst.apdex ? row : worst),
      null,
    )
    return { requests, errors, slowest, worstApdex, count: rows.length }
  }, [rows])

  const columns: Column<EndpointRow>[] = [
    {
      key: 'endpoint',
      header: 'Endpoint',
      width: '30%',
      sortValue: (row) => row.endpoint,
      render: (row) => (
        <div className="flex min-w-0 items-center gap-2">
          <MethodBadge method={row.method} />
          <span className="truncate font-mono text-xs text-ink">{row.endpoint}</span>
        </div>
      ),
    },
    {
      key: 'service',
      header: 'Service',
      hideBelow: 'lg',
      sortValue: (row) => row.service,
      render: (row) => <span className="text-xs text-ink-3">{row.service}</span>,
    },
    {
      key: 'requests',
      header: 'Requests',
      align: 'right',
      sortValue: (row) => row.requests,
      render: (row) => (
        <div className="flex items-center justify-end gap-2">
          <span className="tnum text-xs text-ink">{compact(row.requests)}</span>
          <span className="hidden w-9 text-right text-2xs text-ink-3 sm:inline">{percent(row.traffic_share, 1)}</span>
        </div>
      ),
    },
    {
      key: 'trend',
      header: 'Trend',
      hideBelow: 'md',
      width: '110px',
      render: (row) => (
        <Sparkline
          values={(row.sparkline ?? []).map((point) => point.requests)}
          width={92}
          height={22}
          ariaLabel={`${row.endpoint} request trend`}
        />
      ),
    },
    {
      key: 'error_rate',
      header: 'Errors',
      align: 'right',
      sortValue: (row) => row.error_rate,
      render: (row) => (
        <span
          className={cn(
            'tnum text-xs',
            row.error_rate > 0.08 ? 'text-critical' : row.error_rate > 0.03 ? 'text-warn' : 'text-ink-2',
          )}
        >
          {percent(row.error_rate, 2)}
        </span>
      ),
    },
    {
      key: 'p95',
      header: 'p95',
      align: 'right',
      sortValue: (row) => row.p95_response_time,
      render: (row) => (
        <div className="flex items-center justify-end gap-2">
          <span className="tnum text-xs text-ink">{ms(row.p95_response_time)}</span>
          <span className="hidden h-1 w-10 overflow-hidden rounded-full bg-line sm:block" title="share of a 500 ms budget">
            <span
              className={cn(
                'block h-full rounded-full',
                row.latency_budget > 1 ? 'bg-critical' : row.latency_budget > 0.6 ? 'bg-warn' : 'bg-good',
              )}
              style={{ width: `${Math.min(100, row.latency_budget * 100)}%` }}
            />
          </span>
        </div>
      ),
    },
    {
      key: 'p99',
      header: 'p99',
      align: 'right',
      hideBelow: 'xl',
      sortValue: (row) => row.p99_response_time,
      render: (row) => <span className="tnum text-xs text-ink-2">{ms(row.p99_response_time)}</span>,
    },
    {
      key: 'apdex',
      header: 'Apdex',
      align: 'right',
      hideBelow: 'lg',
      sortValue: (row) => row.apdex,
      render: (row) => (
        <span className={cn('tnum text-xs', row.apdex < 0.8 ? 'text-warn' : 'text-ink-2')}>{row.apdex.toFixed(3)}</span>
      ),
    },
  ]

  return (
    <PageTransition>
      <PageHeader
        title="Endpoints"
        description="Per-route throughput, error rate and latency. Select a row for its full history."
        actions={
          <>
            <SearchInput value={search} onChange={setSearch} placeholder="Filter routes…" className="w-52" />
            <SegmentedControl
              options={SORTS.map((item) => ({ value: item.value, label: item.label }))}
              value={sort}
              onChange={setSort}
              ariaLabel="Sort endpoints"
            />
          </>
        }
      />

      <StatStrip className="mb-4 lg:grid-cols-4">
        <StatTile label="Routes" value={totals.count} footer="matching the current filter" />
        <StatTile label="Requests" value={compact(totals.requests)} footer={`${compact(totals.errors)} failed`} />
        <StatTile
          label="Slowest route"
          value={totals.slowest ? ms(totals.slowest.p95_response_time) : '—'}
          footer={totals.slowest?.endpoint ?? '—'}
          tone="warn"
        />
        <StatTile
          label="Worst Apdex"
          value={totals.worstApdex ? totals.worstApdex.apdex.toFixed(3) : '—'}
          footer={totals.worstApdex?.endpoint ?? '—'}
          tone={(totals.worstApdex?.apdex ?? 1) < 0.8 ? 'critical' : 'default'}
        />
      </StatStrip>

      <Panel title="Route performance" subtitle={`${rows.length} routes`} dense className="mb-4">
        <QueryBoundary isLoading={endpoints.isLoading} error={endpoints.error} onRetry={endpoints.refetch} height={320}>
          <DataTable
            rows={rows}
            columns={columns}
            rowKey={(row) => `${row.method}-${row.endpoint}`}
            onRowClick={setSelected}
            selectedKey={selected ? `${selected.method}-${selected.endpoint}` : null}
            emptyTitle="No endpoints matched"
            emptyMessage="Adjust the filter or widen the time range."
            maxHeight={640}
          />
        </QueryBoundary>
      </Panel>

      <Panel title="Service health" dense bodyClassName="p-0">
        <QueryBoundary isLoading={services.isLoading} error={services.error} height={140}>
          <div className="grid grid-cols-1 divide-y divide-line sm:grid-cols-2 sm:divide-x lg:grid-cols-5 lg:divide-y-0">
            {(services.data?.items ?? []).map((service: any) => (
              <div key={service.service} className="px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium text-ink">{service.service}</span>
                  <span
                    className={cn(
                      'h-1.5 w-1.5 shrink-0 rounded-full',
                      service.status === 'critical' ? 'bg-critical' : service.status === 'degraded' ? 'bg-warn' : 'bg-good',
                    )}
                    title={service.status}
                  />
                </div>
                <p className="tnum mt-1 text-lg font-semibold text-ink">{compact(service.requests)}</p>
                <p className="mt-0.5 text-2xs text-ink-3">
                  {percent(service.error_rate, 2)} errors · {ms(service.p95_response_time)} p95 · {service.hosts} hosts
                </p>
              </div>
            ))}
          </div>
        </QueryBoundary>
      </Panel>

      <EndpointDrawer endpoint={selected} onClose={() => setSelected(null)} />
    </PageTransition>
  )
}

function EndpointDrawer({ endpoint, onClose }: { endpoint: EndpointRow | null; onClose: () => void }) {
  const theme = useChartTheme()
  const detail = useEndpointDetail(endpoint?.endpoint ?? null, endpoint?.method)

  const series = useMemo(
    () =>
      (detail.data?.series ?? []).map((row: any) => ({
        t: row.window_start,
        requests: row.requests,
        p95: row.p95_response_time,
        p50: row.p50_response_time,
        error_rate: row.error_rate,
      })),
    [detail.data],
  )

  const anomalyBands = useMemo(
    () =>
      (detail.data?.anomalies ?? []).map((anomaly) => ({
        start: anomaly.window_start,
        end: anomaly.window_end,
        severity: anomaly.severity,
      })),
    [detail.data],
  )

  return (
    <Drawer
      open={Boolean(endpoint)}
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          <MethodBadge method={endpoint?.method ?? 'GET'} />
          <span className="font-mono text-xs">{endpoint?.endpoint}</span>
        </span>
      }
      subtitle={detail.data?.summary?.service}
      width="max-w-3xl"
    >
      {detail.isLoading ? (
        <SkeletonRows rows={10} className="p-4" />
      ) : detail.isError ? (
        <EmptyState title="Could not load this endpoint" message={(detail.error as Error).message} />
      ) : (
        <div className="space-y-4 p-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: 'Requests', value: compact(detail.data?.summary?.requests ?? 0) },
              { label: 'Error rate', value: percent(detail.data?.summary?.error_rate ?? 0, 2) },
              { label: 'p95', value: ms(detail.data?.summary?.p95_response_time ?? 0) },
              { label: 'Apdex', value: (detail.data?.summary?.apdex ?? 1).toFixed(3) },
            ].map((item) => (
              <div key={item.label} className="rounded border border-line px-3 py-2">
                <p className="label-caps">{item.label}</p>
                <p className="tnum mt-0.5 text-lg font-semibold text-ink">{item.value}</p>
              </div>
            ))}
          </div>

          <div>
            <p className="label-caps mb-2">Throughput and latency</p>
            <TimeSeriesChart
              data={series}
              series={[
                { key: 'requests', label: 'Requests', color: theme.series[0] },
              ]}
              variant="area"
              height={160}
              anomalies={anomalyBands}
              formatValue={(_key, value) => compact(value)}
              showLegend={false}
            />
            <TimeSeriesChart
              data={series}
              series={[
                { key: 'p50', label: 'p50', color: theme.series[2] },
                { key: 'p95', label: 'p95', color: theme.series[3] },
              ]}
              height={150}
              anomalies={anomalyBands}
              formatValue={(_key, value) => ms(value)}
              formatAxis={(value) => ms(value)}
            />
          </div>

          {detail.data?.anomalies?.length ? (
            <div>
              <p className="label-caps mb-2">Detections on this route</p>
              <ul className="space-y-1.5">
                {detail.data.anomalies.slice(0, 6).map((anomaly) => (
                  <li key={anomaly.anomaly_id} className="rounded border border-line px-3 py-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-ink">{anomaly.headline}</p>
                        <p className="mt-0.5 text-2xs text-ink-3">{clock(anomaly.window_start, true)}</p>
                      </div>
                      <SeverityBadge severity={anomaly.severity} score={anomaly.score} size="sm" />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div>
            <p className="label-caps mb-2">Slowest requests</p>
            <ul className="divide-y divide-line rounded border border-line">
              {(detail.data?.slowest_requests ?? []).slice(0, 8).map((event) => (
                <li key={event.event_id} className="flex items-center justify-between gap-3 px-3 py-1.5">
                  <span className="truncate font-mono text-2xs text-ink-2">{event.path}</span>
                  <span className="flex shrink-0 items-center gap-3">
                    <StatusCode code={event.status} />
                    <span className="tnum text-xs text-ink">{ms(event.response_time_ms)}</span>
                    <span className="hidden text-2xs text-ink-3 sm:inline">{clock(event.timestamp, true)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <DefinitionList
            items={[
              { label: 'Bytes served', value: bytes(endpoint?.bytes_sent ?? 0) },
              { label: 'Cache hit rate', value: percent(endpoint?.cache_hit_rate ?? 0, 1) },
              { label: 'Unique clients', value: compact(endpoint?.unique_ips ?? 0) },
              { label: 'Windows observed', value: detail.data?.summary?.windows ?? 0 },
            ]}
          />
        </div>
      )}
    </Drawer>
  )
}
