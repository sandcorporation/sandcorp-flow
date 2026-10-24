import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-transparent text-sm font-medium transition-[filter,background-color] hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'border-primary bg-primary font-semibold text-primary-foreground',
        destructive: 'border-destructive bg-destructive font-semibold text-destructive-foreground',
        outline: 'border-input bg-secondary text-secondary-foreground hover:bg-accent',
        secondary: 'border-line-soft bg-secondary text-secondary-foreground',
        ghost: 'hover:bg-accent hover:text-accent-foreground hover:brightness-100',
        // 머리 막대(어두운 면) 위 버튼 — 시안 .top .btn
        head: 'border-head-foreground/35 bg-transparent text-head-foreground hover:bg-head-foreground/10 hover:brightness-100',
        link: 'text-foreground underline underline-offset-4 hover:brightness-100',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 px-3 text-xs',
        lg: 'h-10 px-8',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
  },
)
Button.displayName = 'Button'

export { buttonVariants }
