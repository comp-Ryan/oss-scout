import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva('inline-flex items-center rounded-sm border px-1.5 py-px text-[11.5px] leading-4', {
  variants: {
    tone: {
      quiet: 'border-line text-muted',
      ink: 'border-line text-ink',
      signal: 'border-signal/60 text-signal',
      teal: 'border-teal/60 text-teal',
      violet: 'border-violet/60 text-violet',
      green: 'border-green/60 text-green',
      bad: 'border-bad/60 text-bad',
    },
  },
  defaultVariants: { tone: 'quiet' },
})

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}
export const Badge = ({ className, tone, ...props }: BadgeProps) => <span className={cn(badgeVariants({ tone }), className)} {...props} />
