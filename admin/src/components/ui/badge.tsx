import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-sm border px-2 py-0.5 font-mono text-[0.7rem] font-medium tracking-[0.02em] transition-colors',
  {
    variants: {
      variant: {
        default: 'border-primary bg-primary text-primary-foreground',
        secondary: 'border-line-soft bg-secondary text-secondary-foreground',
        destructive: 'border-destructive bg-destructive text-destructive-foreground',
        outline: 'text-foreground',
        success: 'border-ok bg-ok/15 text-ok',
        warning: 'border-primary bg-primary/20 text-foreground',
        alarm: 'border-alarm bg-alarm/15 text-alarm',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export function Badge({ className, variant, ...props }: React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof badgeVariants>) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}
