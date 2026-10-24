import * as React from 'react'
import { cn } from '@/lib/utils'

// 좁은 폭(<640px)에서는 카드형으로 접힌다(index.css .flow-table). 열 이름은 머리 칸 글자에서 셀의 data-label로 옮긴다.
export function Table({ className, ...props }: React.HTMLAttributes<HTMLTableElement>) {
  const ref = React.useRef<HTMLTableElement>(null)
  React.useLayoutEffect(() => {
    const table = ref.current
    if (!table) return
    const heads = Array.from(table.querySelectorAll('thead th')).map(th => (th.textContent ?? '').trim())
    table.querySelectorAll('tbody tr').forEach(tr => {
      Array.from(tr.children).forEach((td, i) => {
        if (td.hasAttribute('colspan') || !heads[i]) td.removeAttribute('data-label')
        else td.setAttribute('data-label', heads[i])
      })
    })
  })
  return (
    <div className="flow-table relative w-full overflow-auto">
      <table ref={ref} className={cn('w-full caption-bottom text-sm tabular-nums', className)} {...props} />
    </div>
  )
}
export function TableHeader({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className={cn('bg-sunken [&_tr]:border-b [&_tr]:border-border', className)} {...props} />
}
export function TableBody({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn('[&_tr:last-child]:border-0', className)} {...props} />
}
export function TableRow({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn('border-b border-line-soft transition-colors hover:bg-sunken', className)} {...props} />
}
export function TableHead({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn('h-10 px-3 text-left align-middle font-cond text-[0.72rem] font-semibold tracking-[0.06em] text-muted-foreground', className)} {...props} />
}
export function TableCell({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn('p-3 align-middle', className)} {...props} />
}
