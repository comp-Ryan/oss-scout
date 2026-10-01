import * as S from '@radix-ui/react-scroll-area'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function ScrollArea({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <S.Root className={cn('overflow-hidden', className)}>
      <S.Viewport className="h-full w-full">{children}</S.Viewport>
      <S.Scrollbar orientation="vertical" className="flex w-2 touch-none select-none p-px"><S.Thumb className="flex-1 rounded-full bg-line" /></S.Scrollbar>
    </S.Root>
  )
}
