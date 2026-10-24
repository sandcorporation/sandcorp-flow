import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { useState, useEffect } from 'react'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { tenantUsage } from '../api'
import type { TenantUsageOut } from '../generated/model'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { CostCell, CostTotal, DailyCostTable } from './UsageCost'

// 긴 이름이 툴팁(숨겨져 있어도)으로 390폭 밖까지 밀지 않게(#90)
const TOOLTIP_WRAP = { maxWidth: 260 }
const TOOLTIP_BOX = { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const }

const PERIODS = [{ d: 7, label: '7일' }, { d: 30, label: '30일' }, { d: 90, label: '90일' }]

export default function TenantUsageTab() {
  const [data, setData] = useState<TenantUsageOut | null>(null)
  const [days, setDays] = useState(30)

  useEffect(() => { tenantUsage(days).then(setData) }, [days])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        {PERIODS.map(p => (
          <Button key={p.d} size="sm" variant={days === p.d ? 'default' : 'outline'} onClick={() => setDays(p.d)}>
            {p.label}
          </Button>
        ))}
      </div>

      {!data ? (
        <p className="py-8 text-center text-sm text-muted-foreground">불러오는 중…</p>
      ) : (
        <>
          <Card>
            <CardContent className="pt-6">
              <div className="text-xs text-muted-foreground">총 사용 토큰</div>
              <div data-testid="usage-total" className="text-3xl font-bold">{data.total_tokens.toLocaleString()}</div>
            </CardContent>
          </Card>

          <CostTotal label="총 비용(USD)" cost={data.cost_usd} unknown={data.unknown_cost_models} />

          <Card>
            <CardContent className="space-y-4 pt-6">
              <div className="text-sm font-semibold">유형별</div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>유형</TableHead><TableHead>입력</TableHead><TableHead>출력</TableHead><TableHead>합계</TableHead><TableHead>호출</TableHead><TableHead>비용</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.by_call_type.map(b => (
                    <TableRow key={b.call_type} data-testid="usage-row">
                      <TableCell>{b.call_type}</TableCell>
                      <TableCell>{b.input_tokens.toLocaleString()}</TableCell>
                      <TableCell>{b.output_tokens.toLocaleString()}</TableCell>
                      <TableCell>{b.total_tokens.toLocaleString()}</TableCell>
                      <TableCell>{b.request_count.toLocaleString()}</TableCell>
                      <TableCell><CostCell cost={b.cost_usd} unknown={b.unknown_cost_models.length > 0} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={data.by_call_type}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="call_type" tickFormatter={(v: string) => (v.length > 8 ? `${v.slice(0, 8)}…` : v)} /><YAxis /><Tooltip wrapperStyle={TOOLTIP_WRAP} contentStyle={TOOLTIP_BOX} />
                  <Bar dataKey="total_tokens" fill="hsl(var(--flow-accent))" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <DailyCostTable title="일별 비용" daily={data.daily} />

          <Card>
            <CardContent className="space-y-3 pt-6">
              <div className="text-sm font-semibold">일별 추이</div>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={data.daily}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" /><YAxis /><Tooltip wrapperStyle={TOOLTIP_WRAP} contentStyle={TOOLTIP_BOX} />
                  <Line type="monotone" dataKey="total_tokens" stroke="hsl(var(--flow-accent))" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  )
}
