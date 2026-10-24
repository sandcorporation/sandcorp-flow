import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import * as api from '../api'
import FlowsTab from './FlowsTab'
import { fakeFlowsApi } from '../flowsFakeApi'

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
