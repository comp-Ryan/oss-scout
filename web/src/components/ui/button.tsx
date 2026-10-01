import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-[13px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'bg-signal text-signal-ink hover:bg-[#f0bd5f]',
        secondary: 'bg-raised text-ink hover:bg-line',
        ghost: 'text-muted hover:text-ink hover:bg-raised',
        outline: 'border border-line text-muted hover:text-ink hover:border-muted',
        danger: 'text-bad hover:bg-bad/10',
      },
      size: { sm: 'h-7 px-2.5', md: 'h-8 px-3', lg: 'h-9 px-4', icon: 'h-7 w-7' },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, ...props }, ref) => {
  const Comp = asChild ? Slot : 'button'
  return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
})
Button.displayName = 'Button'
