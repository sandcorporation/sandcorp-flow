import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('../api', () => ({
  tenantUsage: vi.fn().mockResolvedValue({
    total_tokens: 42, cost_usd: '0.012300', unpriced_models: ['mystery-model'], unpriced_requests: 2,
    by_call_type: [
      { call_type: 'chat', input_tokens: 10, output_tokens: 5, total_tokens: 15, request_count: 1, cost_usd: '0.012300', unpriced_models: ['mystery-model'], unpriced_requests: 2 },
      { call_type: 'embedding', input_tokens: 27, output_tokens: 0, total_tokens: 27, request_count: 3, cost_usd: '0.000000', unpriced_models: [], unpriced_requests: 0 },
    ],
    daily: [{ date: '2026-06-24', total_tokens: 30, cost_usd: '0.012300', has_unpriced: true }, { date: '2026-06-25', total_tokens: 12, cost_usd: '0.000000', has_unpriced: false }],
  }),
  operatorUsage: vi.fn().mockResolvedValue({
    total_tokens: 99, cost_usd: '1.500000', unpriced_models: [], unpriced_requests: 0,
    by_tenant: [{ tenant_id: 't1', tenant_name: 'Acme', total_tokens: 99, request_count: 4, cost_usd: '1.500000', unpriced_models: [], unpriced_requests: 0 }],
    daily: [{ date: '2026-06-24', total_tokens: 99, cost_usd: '1.500000', has_unpriced: false }],
  }),
}))

import TenantUsageTab from './TenantUsageTab'
import OperatorUsageTab from './OperatorUsageTab'

describe('TenantUsageTab', () => {
  it('총 토큰과 유형별 행을 렌더한다', async () => {
    render(<TenantUsageTab />)
    expect(await screen.findByTestId('usage-total')).toHaveTextContent('42')
    expect(screen.getByText('chat')).toBeInTheDocument()
    expect(screen.getByText('embedding')).toBeInTheDocument()
    expect(screen.getAllByTestId('usage-row')).toHaveLength(2)
  })

  it('비용 합계·유형별·일별 비용을 보이고, 단가 모름 모델이 있으면 합계가 최소값임을 알린다', async () => {
    render(<TenantUsageTab />)
    expect(await screen.findByTestId('usage-cost')).toHaveTextContent('$0.0123')
    expect(screen.getByTestId('usage-cost-unknown')).toHaveTextContent('mystery-model')
    expect(screen.getByTestId('usage-cost-unknown')).toHaveTextContent('단가 모름')
    expect(screen.getByTestId('usage-cost-unknown')).toHaveTextContent('일부 미포함')
    expect(screen.getAllByTestId('usage-row')[0]).toHaveTextContent('mystery-model')
    const days = screen.getAllByTestId('daily-cost-row')
    expect(days).toHaveLength(2)
    expect(days[0]).toHaveTextContent('2026-06-24')
    expect(days[0]).toHaveTextContent('$0.0123')
    expect(days[0]).toHaveTextContent('일부 미포함')
    expect(days[1]).not.toHaveTextContent('일부 미포함')
  })
})

describe('OperatorUsageTab', () => {
  it('전체 총 토큰과 테넌트별 행을 렌더한다', async () => {
    render(<OperatorUsageTab />)
    expect(await screen.findByTestId('usage-total')).toHaveTextContent('99')
    expect(screen.getByText('Acme')).toBeInTheDocument()
    expect(screen.getAllByTestId('tenant-row')).toHaveLength(1)
  })

  it('전체 비용 합계·테넌트별·일별 비용을 보인다(모르는 모델이 없으면 알림 없음)', async () => {
    render(<OperatorUsageTab />)
    expect(await screen.findByTestId('usage-cost')).toHaveTextContent('$1.5000')
    expect(screen.queryByTestId('usage-cost-unknown')).toBeNull()
    expect(screen.getByTestId('tenant-row')).toHaveTextContent('$1.5000')
    expect(screen.getByTestId('daily-cost-row')).toHaveTextContent('$1.5000')
  })
})
