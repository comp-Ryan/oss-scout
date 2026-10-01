import * as T from '@radix-ui/react-tooltip'
import type { ReactNode } from 'react'

export const TooltipProvider = T.Provider
export function Tip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <T.Root delayDuration={200}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content sideOffset={6} className="z-50 rounded-md border border-line bg-raised px-2.5 py-1.5 text-[12px] text-ink shadow-lg">{content}</T.Content>
      </T.Portal>
    </T.Root>
  )
}
