/** Geography — where traffic originates and which origins look hostile. */

import { lazy, Suspense, useMemo, useState } from 'react'

import { TimeSeriesChart } from '@/charts/TimeSeriesChart'
import { useChartTheme } from '@/charts/palette'
import { GroundTruthBadge } from '@/components/Badges'
import { DataTable, type Column } from '@/components/DataTable'
import { PageHeader } from '@/components/PageHeader'
import { LoadingBlock, Panel, PageTransition, QueryBoundary } from '@/components/Panel'
import { StatStrip, StatTile } from '@/components/StatTile'
import { useGeo, useGeoPoints, useGeoTimeseries, useIps } from '@/hooks/useApi'
import { bytes, cn, compact, ms, number, percent } from '@/lib/format'
import type { GeoRow } from '@/lib/types'

const Globe = lazy(() => import('@/charts/Globe').then((module) => ({ default: module.Globe })))

export function Geography() {
  const theme = useChartTheme()
  const [selected, setSelected] = useState<string[]>([])

  const geo = useGeo()
  const points = useGeoPoints()
  const timeseries = useGeoTimeseries(selected.length ? selected : undefined)
  const ips = useIps({ limit: 12, sort: 'threat_score', suspicious_only: true })

  const rows = geo.data?.items ?? []
  const flagged = rows.filter((row) => row.threat_score > 0)
  const totalCountries = rows.length

  const columns: Column<GeoRow>[] = [
    {
      key: 'country',
      header: 'Origin',
      sortValue: (row) => row.country_name,
      render: (row) => (
        <div className="flex min-w-0 items-center gap-2">
          <span className="font-mono text-2xs text-ink-3">{row.country}</span>
          <span className="truncate text-xs text-ink">{row.country_name}</span>
          <GroundTruthBadge label={row.ground_truth} />
        </div>
      ),
    },
    {
      key: 'requests',
      header: 'Requests',
      align: 'right',
      sortValue: (row) => row.requests,
      render: (row) => (
        <div className="flex items-center justify-end gap-2">
          <span className="tnum text-xs text-ink">{compact(row.requests)}</span>
          <span className="hidden h-1 w-12 overflow-hidden rounded-full bg-line sm:block">
            <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.min(100, row.share * 100)}%` }} />
          </span>
          <span className="tnum hidden w-10 text-right text-2xs text-ink-3 sm:inline">{percent(row.share, 1)}</span>
        </div>
      ),
    },
    {
      key: 'ips',
      header: 'Sources',
      align: 'right',
      hideBelow: 'md',
      sortValue: (row) => row.unique_ips,
      render: (row) => <span className="tnum text-xs text-ink-2">{compact(row.unique_ips)}</span>,
    },
    {
      key: 'error_rate',
      header: 'Errors',
      align: 'right',
      sortValue: (row) => row.error_rate,
      render: (row) => (
        <span className={cn('tnum text-xs', row.error_rate > 0.1 ? 'text-critical' : 'text-ink-2')}>
          {percent(row.error_rate, 2)}
        </span>
      ),
    },
    {
      key: 'p95',
      header: 'p95',
      align: 'right',
      hideBelow: 'lg',
      sortValue: (row) => row.p95_response_time,
      render: (row) => <span className="tnum text-xs text-ink-2">{ms(row.p95_response_time)}</span>,
    },
    {
      key: 'bytes',
      header: 'Egress',
      align: 'right',
      hideBelow: 'xl',
      sortValue: (row) => row.bytes_sent,
      render: (row) => <span className="tnum text-xs text-ink-2">{bytes(row.bytes_sent)}</span>,
    },
    {
      key: 'threat',
      header: 'Threat',
      align: 'right',
      sortValue: (row) => row.threat_score,
      render: (row) =>
        row.threat_score > 0 ? (
          <span className="tnum text-xs font-medium text-critical">{row.threat_score.toFixed(0)}</span>
        ) : (
          <span className="text-2xs text-ink-3">—</span>
        ),
    },
  ]

  const seriesKeys = timeseries.data?.countries ?? []
  const seriesSpecs = useMemo(
    () => seriesKeys.map((code, index) => ({ key: code, label: code, color: theme.series[index % theme.series.length] })),
    [seriesKeys, theme],
  )

  return (
    <PageTransition>
      <PageHeader
        title="Geography"
        description="Traffic origin by country and city, with the origins the detectors have flagged."
      />

      <StatStrip className="mb-4 lg:grid-cols-4">
        <StatTile label="Countries" value={totalCountries} footer="with traffic in range" />
        <StatTile label="Requests" value={compact(geo.data?.total_requests ?? 0)} />
        <StatTile
          label="Flagged origins"
          value={flagged.length}
          tone={flagged.length ? 'critical' : 'good'}
          footer={flagged.slice(0, 3).map((row) => row.country).join(', ') || 'none'}
        />
        <StatTile label="Active cities" value={points.data?.points.length ?? 0} />
      </StatStrip>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-[1fr_400px]">
        <Panel title="Live origin map" subtitle="spike height by volume" dense bodyClassName="p-2">
          <QueryBoundary
            isLoading={points.isLoading}
            error={points.error}
            isEmpty={!points.data?.points.length}
            height={420}
            emptyTitle="No located traffic"
            emptyMessage="Geo enrichment appears once raw logs are stored."
          >
            <Suspense fallback={<LoadingBlock height={420} label="Loading globe" />}>
              <Globe points={points.data?.points ?? []} height={420} />
            </Suspense>
          </QueryBoundary>
        </Panel>

        <Panel title="Flagged sources" subtitle="highest threat score" dense>
          <QueryBoundary isLoading={ips.isLoading} error={ips.error} height={420}>
            {(ips.data?.items ?? []).length === 0 ? (
              <p className="px-4 py-10 text-center text-sm text-ink-3">No source has been flagged in this range.</p>
            ) : (
              <ul className="divide-y divide-line">
                {(ips.data?.items ?? []).map((row) => (
                  <li key={row.ip} className="px-4 py-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate font-mono text-xs text-ink">{row.ip}</p>
                        <p className="mt-0.5 truncate text-2xs text-ink-3">
                          {row.country} · {row.org}
                        </p>
                      </div>
                      <span className="tnum shrink-0 text-xs font-semibold text-critical">
                        {row.threat_score.toFixed(0)}
                      </span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {row.anomaly_types.slice(0, 3).map((type) => (
                        <span key={type} className="chip text-2xs">
                          {type.replace(/_/g, ' ')}
                        </span>
                      ))}
                      <span className="chip text-2xs">{compact(row.requests)} req</span>
                      {row.auth_fail_count > 0 ? (
                        <span className="chip text-2xs">{row.auth_fail_count} auth fails</span>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </QueryBoundary>
        </Panel>
      </div>

      <Panel
        title="Origin traffic over time"
        subtitle={selected.length ? `${selected.length} selected` : 'top origins'}
        className="mb-4"
        actions={
          selected.length ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelected([])}>
              Reset selection
            </button>
          ) : null
        }
      >
        <QueryBoundary
          isLoading={timeseries.isLoading}
          error={timeseries.error}
          isEmpty={!timeseries.data?.points.length}
          height={240}
        >
          <TimeSeriesChart
            data={timeseries.data?.points ?? []}
            series={seriesSpecs}
            height={240}
            bucketSeconds={Math.max(timeseries.data?.range.bucket_seconds ?? 300, 300)}
            formatValue={(_key, value) => number(value)}
          />
        </QueryBoundary>
      </Panel>

      <Panel title="Countries" subtitle="select rows to chart them" dense>
        <QueryBoundary isLoading={geo.isLoading} error={geo.error} onRetry={geo.refetch} height={320}>
          <DataTable
            rows={rows}
            columns={columns}
            rowKey={(row) => row.country + row.country_name}
            defaultSort={{ key: 'requests', direction: 'desc' }}
            maxHeight={520}
            onRowClick={(row) =>
              setSelected((current) =>
                current.includes(row.country)
                  ? current.filter((code) => code !== row.country)
                  : [...current, row.country].slice(-6),
              )
            }
            emptyTitle="No geographic data"
          />
        </QueryBoundary>
      </Panel>
    </PageTransition>
  )
}
