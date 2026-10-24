import { Card, CardContent } from '@/components/ui/card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'

/** 사용량 화면의 비용 표시 — 테넌트·Operator가 같이 쓴다. 단가 표에 없는 모델은 0원이 아니라 「단가 미상」으로 따로 알린다. */
export const fmtUsd = (cost: string) => `$${Number(cost).toFixed(4)}`

export function CostCell({ cost, unknown }: { cost: string; unknown: boolean }) {
  return (
    <>
      {fmtUsd(cost)}
      {unknown && <span className="block text-xs text-alarm">+ 단가 미상</span>}
    </>
  )
}

export function CostTotal({ cost, unknown, label }: { cost: string; unknown: string[]; label: string }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div data-testid="usage-cost" className="text-3xl font-bold">{fmtUsd(cost)}</div>
        {unknown.length > 0 && (
          <p data-testid="usage-cost-unknown" className="mt-1 break-words text-xs text-alarm">
            단가 미상 모델({unknown.join(', ')})은 합계에 없습니다. 실제 비용은 이보다 큽니다.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export type DailyCostRow = { date: string; total_tokens: number; cost_usd: string; has_unknown_cost: boolean }

export function DailyCostTable({ daily, title }: { daily: DailyCostRow[]; title: string }) {
  return (
    <Card>
      <CardContent className="space-y-3 pt-6">
        <div className="text-sm font-semibold">{title}</div>
        <div className="max-h-72 overflow-y-auto">
          <Table>
            <TableHeader>
              <TableRow><TableHead>날짜</TableHead><TableHead>토큰</TableHead><TableHead>비용</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {daily.map(d => (
                <TableRow key={d.date} data-testid="daily-cost-row">
                  <TableCell>{d.date}</TableCell>
                  <TableCell>{d.total_tokens.toLocaleString()}</TableCell>
                  <TableCell><CostCell cost={d.cost_usd} unknown={d.has_unknown_cost} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}
