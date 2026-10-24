import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import * as api from '../api'
import FlowEditorPage from './FlowEditorPage'
import TrialBanner from './TrialBanner'
import { fakeFlowsApi } from '../flowsFakeApi'
import type { FlowGraph } from '../lib/flowGraph'

// 배너는 api 의 getTrial(실제 fetch 경로)을 쓰므로 currentAgentRole 만 바꾼다.
vi.mock('../api', async (orig) => ({ ...(await orig<typeof import('../api')>()), currentAgentRole: vi.fn() }))

const GRAPH: FlowGraph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} },
    { id: 'ai1', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 0 }, config: {} },
  ],
  edges: [{ from: 'start', to: 'ai1' }],
}
const TURN = [
  { event: 'start', data: { flow_id: 'f1' } },
  { event: 'reply', data: { text: '답' } },
  { event: 'done', data: { ran: true, handoff: false, note: '', errors: [], session_id: 's1' } },
]

beforeEach(() => { vi.mocked(api.currentAgentRole).mockReturnValue('admin') })
afterEach(() => { vi.unstubAllGlobals() })

describe('시험 대화 뒤 체험 배너 (#103)', () => {
  it('시험 턴이 끝나면 새로고침 없이 배너의 남은 턴이 줄어든다', async () => {
    let used = 0
    const fake = fakeFlowsApi({ graph: GRAPH, testTurns: [TURN], trial: () => ({ state: 'trial', remaining: 20 - used, limit: 20 }) })
    const origFetch = globalThis.fetch
    vi.stubGlobal('fetch', (...a: Parameters<typeof fetch>) => {
      if (String(a[0]).endsWith('/test/')) used += 1  // 서버는 턴을 끝내며 체험 횟수를 올린다
      return origFetch(...a)
    })
    render(
      <MemoryRouter initialEntries={['/tenant/flows/f1']}>
        <TrialBanner />
        <Routes><Route path="/tenant/flows/:id" element={<FlowEditorPage />} /></Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('trial-banner')).toHaveTextContent('남은 대화 20/20턴')
    await userEvent.click(await screen.findByRole('tab', { name: '시험 대화' }))
    await userEvent.type(await screen.findByLabelText('시험 메시지'), '안녕')
    await userEvent.click(screen.getByRole('button', { name: '보내기' }))
    await screen.findByText('답')
    expect(await screen.findByText(/남은 대화 19\/20턴/)).toBeInTheDocument()
    expect(fake.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/test/'))).toHaveLength(1)
  })
})
