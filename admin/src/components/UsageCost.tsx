import { Card, CardContent } from '@/components/ui/card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'

/** 사용량 화면의 비용 표시 — 테넌트·Operator가 같이 쓴다. 단가를 모르는 호출은 0원이 아니라 「단가 모름」(모델 이름과 함께)·「일부 미포함」으로 알린다. */
export const fmtUsd = (cost: string) => `$${Number(cost).toFixed(4)}`

export function CostCell({ cost, unpriced }: { cost: string; unpriced: string[] | boolean }) {
  const models = Array.isArray(unpriced) ? unpriced : []
  const partial = Array.isArray(unpriced) ? unpriced.length > 0 : unpriced
  return (
    <>
      {fmtUsd(cost)}
      {partial && <span className="block break-words text-xs text-alarm">{`일부 미포함 · 단가 모름${models.length > 0 ? ` (${models.join(', ')})` : ''}`}</span>}
    </>
  )
}

export function CostTotal({ cost, unpriced, requests, label }: { cost: string; unpriced: string[]; requests: number; label: string }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div data-testid="usage-cost" className="text-3xl font-bold">{fmtUsd(cost)}</div>
        {unpriced.length > 0 && (
          <p data-testid="usage-cost-unknown" className="mt-1 break-words text-xs text-alarm">
            일부 미포함 — 단가 모름 모델({unpriced.join(', ')}){requests > 0 ? ` 호출 ${requests.toLocaleString()}건` : ''}은 합계에 없습니다. 실제 비용은 이보다 큽니다.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export type DailyCostRow = { date: string; total_tokens: number; cost_usd: string; has_unpriced: boolean }

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
                  <TableCell><CostCell cost={d.cost_usd} unpriced={d.has_unpriced} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}
