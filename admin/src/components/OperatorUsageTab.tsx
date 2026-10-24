import { useState, useEffect } from 'react'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { operatorUsage } from '../api'
import type { OperatorUsageOut } from '../generated/model'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

const PERIODS = [{ d: 7, label: '7일' }, { d: 30, label: '30일' }, { d: 90, label: '90일' }]

export default function OperatorUsageTab() {
  const [data, setData] = useState<OperatorUsageOut | null>(null)
  const [days, setDays] = useState(30)

  useEffect(() => { operatorUsage(days).then(setData) }, [days])

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
              <div className="text-xs text-muted-foreground">전체 테넌트 총 토큰</div>
              <div data-testid="usage-total" className="text-3xl font-bold">{data.total_tokens.toLocaleString()}</div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-4 pt-6">
              <div className="text-sm font-semibold">테넌트별</div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted-foreground"><th className="pb-1">테넌트</th><th>토큰</th><th>호출</th></tr>
                </thead>
                <tbody>
                  {data.by_tenant.map(t => (
                    <tr key={t.tenant_id} data-testid="tenant-row" className="border-t border-border">
                      <td className="py-1.5">{t.tenant_name}</td>
                      <td>{t.total_tokens.toLocaleString()}</td>
                      <td>{t.request_count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={data.by_tenant}>
                  <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="tenant_name" /><YAxis /><Tooltip />
                  <Bar dataKey="total_tokens" fill="hsl(var(--flow-accent))" radius={[2, 2, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 pt-6">
              <div className="text-sm font-semibold">일별 추이(전체)</div>
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
