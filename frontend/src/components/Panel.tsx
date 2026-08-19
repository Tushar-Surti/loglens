/** Panel container plus the loading / empty / error states every view needs. */

import { motion } from 'framer-motion'
import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/format'

export function Panel({
  title,
  subtitle,
  actions,
  children,
  footer,
  className,
  bodyClassName,
  dense,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  footer?: ReactNode
  className?: string
  bodyClassName?: string
  dense?: boolean
}) {
  return (
    <section className={cn('panel flex min-w-0 flex-col', className)}>
      {title ? (
        <header className="panel-header shrink-0">
          <div className="flex min-w-0 items-baseline gap-2">
            <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>
            {subtitle ? <span className="truncate text-xs text-ink-3">{subtitle}</span> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cn('min-w-0 flex-1', dense ? '' : 'p-4', bodyClassName)}>{children}</div>
      {footer ? <footer className="border-t border-line px-4 py-2 text-xs text-ink-3">{footer}</footer> : null}
    </section>
  )
}

export function LoadingBlock({ height = 200, label }: { height?: number; label?: string }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2"
      style={{ height }}
      role="status"
      aria-live="polite"
    >
      <div className="skeleton h-full w-full rounded" />
      {label ? <span className="sr-only">{label}</span> : null}
    </div>
  )
}

export function SkeletonRows({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton h-8 w-full" style={{ opacity: 1 - index * 0.08 }} />
      ))}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  compact: isCompact,
}: {
  error: unknown
  onRetry?: () => void
  compact?: boolean
}) {
  const message = error instanceof Error ? error.message : 'Something went wrong'
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center gap-2 text-center',
        isCompact ? 'py-6' : 'py-12',
      )}
      role="alert"
    >
      <AlertTriangle className="h-4 w-4 text-critical" aria-hidden />
      <p className="text-sm font-medium text-ink">Could not load this view</p>
      <p className="max-w-sm text-xs text-ink-3">{message}</p>
      {onRetry ? (
        <button type="button" className="btn btn-sm mt-1" onClick={onRetry}>
          <RefreshCw className="h-3 w-3" aria-hidden />
          Retry
        </button>
      ) : null}
    </div>
  )
}

export function EmptyState({
  title = 'Nothing here yet',
  message,
  icon,
  action,
}: {
  title?: string
  message?: string
  icon?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <span className="text-ink-3" aria-hidden>
        {icon ?? <Inbox className="h-5 w-5" />}
      </span>
      <p className="text-sm font-medium text-ink-2">{title}</p>
      {message ? <p className="max-w-sm text-xs text-ink-3">{message}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}

/**
 * One place that decides what to render for a query: skeleton, error, empty or
 * content. Keeps every page consistent instead of ad-hoc ternaries.
 */
export function QueryBoundary({
  isLoading,
  error,
  isEmpty,
  onRetry,
  height,
  emptyTitle,
  emptyMessage,
  children,
}: {
  isLoading: boolean
  error: unknown
  isEmpty?: boolean
  onRetry?: () => void
  height?: number
  emptyTitle?: string
  emptyMessage?: string
  children: ReactNode
}) {
  if (isLoading) return <LoadingBlock height={height ?? 200} label="Loading" />
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  if (isEmpty) return <EmptyState title={emptyTitle} message={emptyMessage} />
  return <>{children}</>
}

/** Page-level entrance: a 4px lift, once, never on data refresh. */
export function PageTransition({ children }: { children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
      className="min-w-0"
    >
      {children}
    </motion.div>
  )
}
