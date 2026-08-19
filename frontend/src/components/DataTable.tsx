/**
 * Generic, sortable, keyboard-navigable table.
 *
 * Every list view in the product is a table view — which is also how the charts
 * stay accessible: any encoding shown as colour is reachable as a number here.
 */

import { ChevronDown, ChevronUp, ChevronsUpDown } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { EmptyState, SkeletonRows } from '@/components/Panel'
import { cn } from '@/lib/format'

export interface Column<T> {
  key: string
  header: ReactNode
  render: (row: T, index: number) => ReactNode
  /** Value used for sorting; omit to make the column unsortable. */
  sortValue?: (row: T) => number | string
  align?: 'left' | 'right' | 'center'
  width?: string
  className?: string
  hideBelow?: 'sm' | 'md' | 'lg' | 'xl'
}

interface Props<T> {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T, index: number) => string
  onRowClick?: (row: T) => void
  isLoading?: boolean
  emptyTitle?: string
  emptyMessage?: string
  defaultSort?: { key: string; direction: 'asc' | 'desc' }
  stickyHeader?: boolean
  maxHeight?: number
  selectedKey?: string | null
  className?: string
}

const HIDE_CLASS = {
  sm: 'hidden sm:table-cell',
  md: 'hidden md:table-cell',
  lg: 'hidden lg:table-cell',
  xl: 'hidden xl:table-cell',
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  onRowClick,
  isLoading,
  emptyTitle,
  emptyMessage,
  defaultSort,
  stickyHeader = true,
  maxHeight,
  selectedKey,
  className,
}: Props<T>) {
  const [sort, setSort] = useState(defaultSort ?? null)

  const sorted = useMemo(() => {
    if (!sort) return rows
    const column = columns.find((item) => item.key === sort.key)
    if (!column?.sortValue) return rows
    const factor = sort.direction === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const left = column.sortValue!(a)
      const right = column.sortValue!(b)
      if (typeof left === 'number' && typeof right === 'number') return (left - right) * factor
      return String(left).localeCompare(String(right)) * factor
    })
  }, [rows, columns, sort])

  function toggle(key: string) {
    setSort((current) => {
      if (current?.key !== key) return { key, direction: 'desc' }
      if (current.direction === 'desc') return { key, direction: 'asc' }
      return null
    })
  }

  if (isLoading) return <SkeletonRows rows={8} className="p-4" />
  if (!rows.length) return <EmptyState title={emptyTitle} message={emptyMessage} />

  return (
    <div
      className={cn('min-w-0 overflow-auto', className)}
      style={maxHeight ? { maxHeight } : undefined}
    >
      <table className="w-full border-collapse">
        <thead className={cn(stickyHeader && 'sticky top-0 z-10')}>
          <tr className="bg-surface">
            {columns.map((column) => {
              const sortable = Boolean(column.sortValue)
              const active = sort?.key === column.key
              return (
                <th
                  key={column.key}
                  className={cn(
                    'th border-b border-line bg-surface',
                    column.align === 'right' && 'text-right',
                    column.align === 'center' && 'text-center',
                    column.hideBelow && HIDE_CLASS[column.hideBelow],
                  )}
                  style={column.width ? { width: column.width } : undefined}
                  aria-sort={active ? (sort!.direction === 'asc' ? 'ascending' : 'descending') : 'none'}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => toggle(column.key)}
                      className={cn(
                        'inline-flex items-center gap-1 transition-colors hover:text-ink-2',
                        active && 'text-ink-2',
                        column.align === 'right' && 'flex-row-reverse',
                      )}
                    >
                      {column.header}
                      {active ? (
                        sort!.direction === 'asc' ? (
                          <ChevronUp className="h-3 w-3" aria-hidden />
                        ) : (
                          <ChevronDown className="h-3 w-3" aria-hidden />
                        )
                      ) : (
                        <ChevronsUpDown className="h-3 w-3 opacity-40" aria-hidden />
                      )}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, index) => {
            const key = rowKey(row, index)
            return (
              <tr
                key={key}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={
                  onRowClick
                    ? (event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          onRowClick(row)
                        }
                      }
                    : undefined
                }
                tabIndex={onRowClick ? 0 : undefined}
                className={cn(
                  'border-b border-line/70 row-hover',
                  onRowClick && 'cursor-pointer',
                  selectedKey === key && 'bg-accent/8',
                )}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(
                      'cell',
                      column.align === 'right' && 'text-right',
                      column.align === 'center' && 'text-center',
                      column.hideBelow && HIDE_CLASS[column.hideBelow],
                      column.className,
                    )}
                  >
                    {column.render(row, index)}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
