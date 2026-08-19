/** Right-hand detail drawer used for log, anomaly and IP inspection. */

import { AnimatePresence, motion } from 'framer-motion'
import { X } from 'lucide-react'
import { useEffect } from 'react'
import type { ReactNode } from 'react'

import { cn } from '@/lib/format'

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  actions,
  children,
  width = 'max-w-2xl',
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  width?: string
}) {
  useEffect(() => {
    if (!open) return
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', listener)
    // Prevent the page behind from scrolling under the drawer.
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', listener)
      document.body.style.overflow = previous
    }
  }, [open, onClose])

  return (
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
          <motion.div
            className="absolute inset-0 bg-black/45 backdrop-blur-[2px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            onClick={onClose}
          />
          <motion.aside
            className={cn('relative flex h-full w-full flex-col border-l border-line bg-surface', width)}
            initial={{ x: 32, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 32, opacity: 0 }}
            transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
          >
            <header className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-4 py-3">
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>
                {subtitle ? <p className="mt-0.5 truncate text-xs text-ink-3">{subtitle}</p> : null}
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {actions}
                <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close">
                  <X className="h-4 w-4" aria-hidden />
                </button>
              </div>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
          </motion.aside>
        </div>
      ) : null}
    </AnimatePresence>
  )
}

export function DefinitionList({
  items,
  columns = 2,
}: {
  items: { label: string; value: ReactNode; mono?: boolean; span?: boolean }[]
  columns?: 1 | 2 | 3
}) {
  return (
    <dl
      className={cn(
        'grid gap-x-6 gap-y-3',
        columns === 1 ? 'grid-cols-1' : columns === 2 ? 'sm:grid-cols-2' : 'sm:grid-cols-3',
      )}
    >
      {items.map((item) => (
        <div key={item.label} className={cn('min-w-0', item.span && 'sm:col-span-full')}>
          <dt className="label-caps">{item.label}</dt>
          <dd className={cn('mt-0.5 break-words text-sm text-ink', item.mono && 'font-mono text-xs')}>
            {item.value ?? '—'}
          </dd>
        </div>
      ))}
    </dl>
  )
}
