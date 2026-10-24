import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import * as api from '../api'
import FlowsTab from './FlowsTab'
import { fakeFlowsApi, type FakeRequest } from '../flowsFakeApi'

vi.mock('../api', () => ({ currentAgentRole: vi.fn() }))

function Where() { return <p data-testid="where">{useLocation().pathname}</p> }
function renderList() {
  return render(
    <MemoryRouter initialEntries={['/tenant/flows']}>
      <Routes>
        <Route path="/tenant/flows" element={<FlowsTab />} />
        <Route path="/tenant/flows/:id" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
}
beforeEach(() => { vi.mocked(api.currentAgentRole).mockReturnValue('admin') })
afterEach(() => { vi.unstubAllGlobals() })

describe('FlowsTab — 흐름 목록', () => {
  it('흐름 이름과 배포 상태를 보이고 이름을 누르면 편집기로 간다', async () => {
    fakeFlowsApi({ graph: { nodes: [], edges: [] }, flows: [
      { id: 'f1', name: '기본 흐름', is_public: false, deployed_version: 3, updated_at: '2026-10-01T09:00:00Z', has_pending_request: false },
      { id: 'f9', name: '초안만', is_public: false, deployed_version: null, updated_at: '2026-10-01T09:00:00Z', has_pending_request: false },
    ] })
    renderList()
    expect(await screen.findByText('v3 배포 중')).toBeInTheDocument()
    expect(screen.getByText('배포 전')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: '기본 흐름' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/tenant/flows/f1')
  })

  it('새 흐름을 만들면 그 편집기로 간다', async () => {
    const fake = fakeFlowsApi({ graph: { nodes: [], edges: [] } })
    renderList()
    await userEvent.type(await screen.findByLabelText('새 흐름 이름'), '환불 안내')
    await userEvent.click(screen.getByRole('button', { name: '만들기' }))
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/tenant/flows/f2'))
    expect(fake.calls.find((c) => c.method === 'POST')?.body).toEqual({ name: '환불 안내' })
  })

  it('멤버(권한 없음)에게는 만들기 폼이 없다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    fakeFlowsApi({ graph: { nodes: [], edges: [] } })
    renderList()
    await screen.findByRole('link', { name: '기본 흐름' })
    expect(screen.queryByLabelText('새 흐름 이름')).toBeNull()
  })
})

const REQ = (o: Partial<FakeRequest> = {}): FakeRequest => ({ id: 'r1', flow_id: 'f1', flow_name: '기본 흐름', version: 2, status: 'pending', requested_by_name: '박둘째', decided_by_name: null, decided_at: null, decision_note: '', created_at: '2026-10-02T11:00:00Z', ...o })

describe('FlowsTab — 배포 요청', () => {
  const me = { name: '나', email: 'me@x.kr' }
  it('승인 대기 요청이 있으면 흐름 이름·버전·요청자와 함께 목록으로 보이고, 승인하면 사라진다', async () => {
    const fake = fakeFlowsApi({ graph: { nodes: [], edges: [] }, requests: [REQ()], me })
    renderList()
    const region = within(await screen.findByRole('region', { name: '배포 요청' }))
    expect(await region.findByText(/기본 흐름 v2 승인 대기/)).toBeInTheDocument()
    expect(region.getByText(/박둘째 요청/)).toBeInTheDocument()
    await userEvent.click(region.getByRole('button', { name: '승인' }))
    await waitFor(() => expect(fake.calls.some((c) => c.path.endsWith('/r1/approve/'))).toBe(true))
    await waitFor(() => expect(screen.queryByText(/v2 승인 대기/)).toBeNull())
  })

  it('본인 요청은 승인이 꺼지고 이유가 보인다', async () => {
    fakeFlowsApi({ graph: { nodes: [], edges: [] }, requests: [REQ({ requested_by_name: '나' })], me })
    renderList()
    const region = within(await screen.findByRole('region', { name: '배포 요청' }))
    expect(await region.findByRole('button', { name: '승인' })).toBeDisabled()
    expect(region.getByText(/다른 관리자가 승인/)).toBeInTheDocument()
  })

  it('대기 요청이 없으면 구획을 숨긴다', async () => {
    fakeFlowsApi({ graph: { nodes: [], edges: [] }, me })
    renderList()
    await screen.findByText('기본 흐름')
    expect(screen.queryByRole('region', { name: '배포 요청' })).toBeNull()
  })

  it('Member 는 요청을 볼 수 있지만 승인·거절은 꺼진다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    fakeFlowsApi({ graph: { nodes: [], edges: [] }, requests: [REQ()], me })
    renderList()
    const region = within(await screen.findByRole('region', { name: '배포 요청' }))
    expect(await region.findByRole('button', { name: '승인' })).toBeDisabled()
    expect(region.getByRole('button', { name: '거절' })).toBeDisabled()
    expect(region.getByText(/관리자만/)).toBeInTheDocument()
  })
})
