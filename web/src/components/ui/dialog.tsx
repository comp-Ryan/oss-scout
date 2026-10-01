import * as React from 'react'
import * as D from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

export const Dialog = D.Root
export const DialogTrigger = D.Trigger
export function DialogContent({ className, children, title, ...props }: React.ComponentPropsWithoutRef<typeof D.Content> & { title: string }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-40 bg-black/60" />
      <D.Content className={cn('fixed left-1/2 top-1/2 z-50 w-[min(560px,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-line bg-surface p-4 shadow-xl', className)} {...props}>
        <div className="mb-3 flex items-center justify-between">
          <D.Title className="text-[15px] font-semibold">{title}</D.Title>
          <D.Close className="rounded-sm p-1 text-muted hover:text-ink" aria-label="close"><X className="h-4 w-4" /></D.Close>
        </div>
        {children}
      </D.Content>
    </D.Portal>
  )
}
