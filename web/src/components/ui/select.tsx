import * as S from '@radix-ui/react-select'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Select({ value, onValueChange, options, label, className }: {
  value: string; onValueChange: (v: string) => void; options: { value: string; label: string }[]; label: string; className?: string
}) {
  return (
    <S.Root value={value} onValueChange={onValueChange}>
      <S.Trigger aria-label={label} className={cn('inline-flex h-7 items-center gap-1 rounded-md border border-line bg-bg px-2 text-[12.5px] text-ink', className)}>
        <S.Value /><ChevronDown className="h-3.5 w-3.5 text-muted" />
      </S.Trigger>
      <S.Portal>
        <S.Content position="popper" sideOffset={4} className="z-50 min-w-[160px] rounded-md border border-line bg-raised p-1 shadow-lg">
          <S.Viewport>
            {options.map(o => (
              <S.Item key={o.value} value={o.value} className="relative flex cursor-pointer select-none items-center rounded-sm py-1 pl-6 pr-2 text-[12.5px] text-ink outline-none data-[highlighted]:bg-line">
                <S.ItemIndicator className="absolute left-1.5"><Check className="h-3 w-3" /></S.ItemIndicator>
                <S.ItemText>{o.label}</S.ItemText>
              </S.Item>
            ))}
          </S.Viewport>
        </S.Content>
      </S.Portal>
    </S.Root>
  )
}
