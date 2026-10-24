import { useState, useEffect } from 'react'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { tenantUsage } from '../api'
import type { TenantUsageOut } from '../generated/model'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

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

          <Card>
            <CardContent className="space-y-4 pt-6">
              <div className="text-sm font-semibold">유형별</div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted-foreground">
                    <th className="pb-1">유형</th><th>입력</th><th>출력</th><th>합계</th><th>호출</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_call_type.map(b => (
                    <tr key={b.call_type} data-testid="usage-row" className="border-t border-border">
                      <td className="py-1.5">{b.call_type}</td>
                      <td>{b.input_tokens.toLocaleString()}</td>
                      <td>{b.output_tokens.toLocaleString()}</td>
                      <td>{b.total_tokens.toLocaleString()}</td>
                      <td>{b.request_count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={data.by_call_type}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="call_type" /><YAxis /><Tooltip />
                  <Bar dataKey="total_tokens" fill="hsl(var(--flow-accent))" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 pt-6">
              <div className="text-sm font-semibold">일별 추이</div>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={data.daily}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="date" /><YAxis /><Tooltip />
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
