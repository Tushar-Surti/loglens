/** Page title block: name, one-line explanation of what this view is for, actions. */

import type { ReactNode } from 'react'

import { cn } from '@/lib/format'

export function PageHeader({
  title,
  description,
  actions,
  meta,
  className,
}: {
  title: string
  description?: string
  actions?: ReactNode
  meta?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('mb-4 flex flex-wrap items-end justify-between gap-3', className)}>
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description ? <p className="mt-0.5 max-w-2xl text-sm text-ink-3">{description}</p> : null}
        {meta ? <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}
