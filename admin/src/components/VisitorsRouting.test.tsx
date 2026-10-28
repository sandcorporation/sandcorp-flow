import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import * as api from '../api'
import VisitorsTab from './VisitorsTab'
import SessionDetailPage from './SessionDetailPage'

vi.mock('../api')

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listVisitors).mockResolvedValue([] as any)
  vi.mocked(api.listVisitorSessions).mockResolvedValue([] as any)
  vi.mocked(api.listMemories).mockResolvedValue([] as any)
  vi.mocked(api.getSessionMessages).mockResolvedValue([] as any)
  vi.mocked(api.getSessionCheckpoint).mockResolvedValue(null as any)
  vi.mocked(api.getSessionRetrievals).mockResolvedValue([] as any)
})

function at(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/tenant/visitors/:visitorId" element={<VisitorsTab />} />
        <Route path="/tenant/sessions/:sessionId" element={<SessionDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('방문자 세션 — 흐름 칸·필터', () => {
  it('세션 행에 흐름 이름(옛 세션은 —)이 보이고 필터를 고르면 flow_id로 다시 부른다', async () => {
    vi.mocked(api.listFlowOptions).mockResolvedValue([{ id: 'f1', name: '봄 이벤트' }, { id: 'f2', name: '환불 안내' }])
    vi.mocked(api.listVisitorSessions).mockResolvedValue([
      { session_id: 'aaaaaaaa-1', visitor_id: 'v', is_hitl: false, created_at: '2026-06-22T00:00:00Z', flow_id: 'f1', flow_name: '봄 이벤트' },
      { session_id: 'bbbbbbbb-2', visitor_id: 'v', is_hitl: false, created_at: '2026-06-22T00:00:00Z', flow_id: null, flow_name: null },
    ] as any)
    at('/tenant/visitors/v')
    expect((await screen.findAllByTestId('flow-name')).map(e => e.textContent)).toEqual(['봄 이벤트', '—'])
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: '흐름' }), 'f2')
    await waitFor(() => expect(api.listVisitorSessions).toHaveBeenLastCalledWith('v', 'f2'))
  })
})

describe('Visitors/Session 자원 라우트(ADR-0017)', () => {
  it('/tenant/visitors/:visitorId가 그 방문자 뷰를 렌더한다(딥링크)', async () => {
    at('/tenant/visitors/visitor-abc')
    expect(await screen.findByText('visitor-abc')).toBeInTheDocument()
  })

  it('/tenant/sessions/:sessionId가 그 세션 상세를 렌더한다(딥링크)', async () => {
    at('/tenant/sessions/sess-12345678')
    expect(await screen.findByText(/sess-123/)).toBeInTheDocument()
  })
})
