import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import * as api from '../api'
import FlowEditorPage from './FlowEditorPage'
import { fakeFlowsApi } from '../flowsFakeApi'
import type { FlowGraph } from '../lib/flowGraph'

vi.mock('../api', () => ({ currentAgentRole: vi.fn() }))

const GRAPH: FlowGraph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} },
    { id: 'ai1', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 0 }, config: { instructions: '친절하게' } },
    { id: 'side', kind: 'handoff', code: 'N-03', name: '상담원', position: { x: 520, y: 0 }, config: { reason: '' } },
  ],
  edges: [{ from: 'start', to: 'ai1' }],
}
const ent = (o: object) => ({ node_id: 'start', kind: 'start', handle: 'next', record: {}, duration_ms: 2, input_tokens: 0, output_tokens: 0, cost_usd: null, calls: [], ...o })
const turn = (reply: string, cost: string | null) => [
  { event: 'start', data: { flow_id: 'f1' } },
  { event: 'trace', data: ent({}) },
  { event: 'trace', data: ent({ node_id: 'ai1', kind: 'ai_answer', duration_ms: 40, input_tokens: 120, output_tokens: 30, cost_usd: cost, calls: cost === null ? [{ model: 'mystery-model', input_tokens: 120, output_tokens: 30, cost_usd: null }] : [], record: { reason: '지식에서 근거를 찾아 답함', output: reply } }) },
  { event: 'trace', data: ent({ node_id: '__flow_finish__', kind: 'finish', handle: '' }) },
  { event: 'reply', data: { text: reply } },
  { event: 'done', data: { ran: true, handoff: false, note: '', errors: [], session_id: 's1' } },
]

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/tenant/flows/f1']}>
      <Routes><Route path="/tenant/flows/:id" element={<FlowEditorPage />} /></Routes>
    </MemoryRouter>,
  )
}
async function openTab(name: string) {
  const aside = await screen.findByRole('complementary', { name: '노드 설정' })
  await userEvent.click(await within(aside).findByRole('tab', { name }))
}
async function say(text: string) {
  await userEvent.type(await screen.findByLabelText('시험 메시지'), text)
  await userEvent.click(screen.getByRole('button', { name: '보내기' }))
}
const tests = (fake: ReturnType<typeof fakeFlowsApi>) => fake.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/test/'))

beforeEach(() => { vi.mocked(api.currentAgentRole).mockReturnValue('admin') })
afterEach(() => { vi.unstubAllGlobals() })

describe('편집기 오른쪽 — 시험 대화', () => {
  it('바깥 탭은 시험 대화 · 판단 기록 · 노드 설정', async () => {
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    const tabs = await within(await screen.findByRole('complementary', { name: '노드 설정' })).findAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['시험 대화', '판단 기록', '노드 설정'])
  })

  it('초안으로 한 턴을 돌려 답을 보인다 — 조각난 스트림도 읽는다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, testTurns: [turn('환불은 7일 이내입니다', '0.0001')] })
    renderEditor()
    await openTab('시험 대화')
    await say('환불해줘')
    expect(await screen.findByText('환불은 7일 이내입니다')).toBeInTheDocument()
    expect(screen.getByText('환불해줘')).toBeInTheDocument()
    expect(tests(fake)[0].body).toEqual({ message: '환불해줘' })
    expect(tests(fake)[0].path).toBe('/api/tenant/flows/f1/test/')
  })

  it('같은 대화로 이어 가며(session_id 재전송) 「새 대화」로 처음부터 시작한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, testTurns: [turn('첫 답', '0.0001'), turn('둘째 답', null)] })
    renderEditor()
    await openTab('시험 대화')
    await say('하나')
    await screen.findByText('첫 답')
    await say('둘')
    await screen.findByText('둘째 답')
    expect(tests(fake)[1].body).toEqual({ message: '둘', session_id: 's1' })
    await userEvent.click(screen.getByRole('button', { name: '새 대화' }))
    expect(screen.queryByText('첫 답')).toBeNull()
    await say('셋')
    await waitFor(() => expect(tests(fake)).toHaveLength(3))
    expect(tests(fake)[2].body).toEqual({ message: '셋' })
  })

  it('체험 턴을 다 썼으면 사람이 읽을 문구로 알린다', async () => {
    fakeFlowsApi({
      graph: GRAPH, trial: { state: 'exhausted', remaining: 0, limit: 20 },
      testTurns: [[{ event: 'start', data: { flow_id: 'f1' } }, { event: 'done', data: { ran: false, handoff: false, note: '체험 턴이 남지 않았거나…', errors: [], session_id: 's1' } }]],
    })
    renderEditor()
    await openTab('시험 대화')
    await say('안녕')
    expect(await screen.findByText(/체험 턴을 모두 썼습니다/)).toBeInTheDocument()
  })

  it('초안이 무효하면 오류를 보이고 고치라고 한다', async () => {
    fakeFlowsApi({
      graph: GRAPH,
      testTurns: [[{ event: 'start', data: { flow_id: 'f1' } }, { event: 'done', data: { ran: false, handoff: false, note: '', errors: [{ node_id: 'side', code: 'unreachable', message: '시작에서 닿지 않습니다' }], session_id: null } }]],
    })
    renderEditor()
    await openTab('시험 대화')
    await say('안녕')
    expect(await screen.findByText(/초안을 고친 뒤/)).toBeInTheDocument()
    expect(screen.getByText(/시작에서 닿지 않습니다/)).toBeInTheDocument()
  })

  it('권한이 없는 멤버는 읽기만 — 입력이 막히고 요청을 보내지 않는다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    const fake = fakeFlowsApi({ graph: GRAPH, testTurns: [turn('x', null)] })
    renderEditor()
    await openTab('시험 대화')
    expect(await screen.findByLabelText('시험 메시지')).toBeDisabled()
    expect(screen.getByRole('button', { name: '보내기' })).toBeDisabled()
    expect(screen.getByText(/관리자만 시험 대화를 돌릴 수 있습니다/)).toBeInTheDocument()
    expect(tests(fake)).toHaveLength(0)
  })

  it('서버가 403이면 권한 문구', async () => {
    fakeFlowsApi({ graph: GRAPH, testStatus: 403 })
    renderEditor()
    await openTab('시험 대화')
    await say('안녕')
    expect(await screen.findByText(/시험 대화를 돌릴 권한이 없습니다/)).toBeInTheDocument()
  })
})

describe('편집기 오른쪽 — 판단 기록', () => {
  async function twoTurns() {
    const fake = fakeFlowsApi({ graph: GRAPH, testTurns: [turn('첫 답', '0.0001'), turn('둘째 답', null)] })
    renderEditor()
    await openTab('시험 대화')
    await say('하나')
    await screen.findByText('첫 답')
    await say('둘')
    await screen.findByText('둘째 답')
    await openTab('판단 기록')
    return fake
  }

  it('턴마다 노드 항목(토큰·비용·시간)과 턴·세션 합계, 비용을 모르면 단가 모름, 내부 끝 노드는 「끝」', async () => {
    await twoTurns()
    const log = screen.getByRole('region', { name: '판단 기록' })
    expect(within(log).getAllByText(/^턴 \d+$/).map((e) => e.textContent)).toEqual(['턴 1', '턴 2'])
    expect(within(log).getAllByText('N-02 답변')).toHaveLength(2)
    expect(within(log).getAllByText('120/30')).toHaveLength(2)
    expect(within(log).getByText('$0.0001')).toBeInTheDocument()
    expect(within(log).getAllByText('단가 모름 (모델 mystery-model)').length).toBeGreaterThan(0)
    expect(within(log).getByText(/세션 합계/).textContent).toMatch(/\$0\.0001 · 일부 미포함 \(모델 mystery-model\)/)
    expect(within(log).getAllByText('끝')).toHaveLength(2)
    expect(log.textContent).not.toContain('__flow_finish__')
    expect(within(log).getByText(/세션 합계/).textContent).toMatch(/240\/60/)
    expect(within(log).getAllByText('지식에서 근거를 찾아 답함').length).toBeGreaterThan(0)
  })

  it('항목을 누르면 캔버스의 그 노드를 강조하고 그 턴 경로를 함께 켠다', async () => {
    await twoTurns()
    const log = screen.getByRole('region', { name: '판단 기록' })
    expect(document.querySelector('[data-node-id="ai1"]')).not.toHaveAttribute('data-active')
    fireEvent.click(within(log).getAllByRole('button', { name: /N-02 답변/ })[0])
    expect(document.querySelector('[data-node-id="ai1"]')).toHaveAttribute('data-active', 'true')
    expect(document.querySelector('[data-node-id="start"]')).toHaveAttribute('data-active', 'true')
    expect(document.querySelector('[data-node-id="side"]')).not.toHaveAttribute('data-active')
  })

  it('시험을 아직 안 했으면 안내', async () => {
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await openTab('판단 기록')
    expect(screen.getByText(/시험 대화를 돌리면/)).toBeInTheDocument()
  })
})


describe('편집기 — 새로고침 뒤 판단 기록 복원', () => {
  const stored = (n: number, reply: string) => ({
    turn: n, created_at: '2026-10-01T09:00:00Z', is_test: true, flow_version: null, input_tokens: 120, output_tokens: 30, cost_usd: '0.0001', duration_ms: 42,
    entries: [ent({ record: { input: '하나' } }), ent({ node_id: 'ai1', kind: 'ai_answer', duration_ms: 40, input_tokens: 120, output_tokens: 30, cost_usd: '0.0001', record: { reason: '근거를 찾아 답함', output: reply } })],
  })

  beforeEach(() => { window.localStorage.clear() })

  it('시험 한 턴 뒤 새로 열어도(새로고침) 마지막 시험 세션의 판단 기록과 대화가 되돌아온다', async () => {
    const first = fakeFlowsApi({ graph: GRAPH, testTurns: [turn('첫 답', '0.0001')] })
    const view = renderEditor()
    await openTab('시험 대화')
    await say('하나')
    await screen.findByText('첫 답')
    view.unmount()

    const second = fakeFlowsApi({ graph: GRAPH, traces: { s1: { turns: [stored(1, '첫 답')] } } })
    renderEditor()
    await openTab('판단 기록')
    const log = await screen.findByRole('region', { name: '판단 기록' })
    await waitFor(() => expect(within(log).getByText('턴 1')).toBeInTheDocument())
    expect(within(log).getByText('$0.0001')).toBeInTheDocument()
    expect(second.calls.some((c) => c.method === 'GET' && c.path.includes('/sessions/s1/decision-traces'))).toBe(true)
    expect(first.calls.length).toBeGreaterThan(0)
    await openTab('시험 대화')
    expect(await screen.findByText('첫 답')).toBeInTheDocument()
  })

  it('기억한 세션이 사라졌으면(404) 조용히 비운다 — 「새 대화」는 기억도 지운다', async () => {
    window.localStorage.setItem('flow-test-session:f1', 'gone')
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await openTab('판단 기록')
    expect(await screen.findByText(/시험 대화를 돌리면/)).toBeInTheDocument()
    await waitFor(() => expect(window.localStorage.getItem('flow-test-session:f1')).toBeNull())
  })

  it('초안마다 따로 기억한다', async () => {
    window.localStorage.setItem('flow-test-session:other', 's1')
    const fake = fakeFlowsApi({ graph: GRAPH, traces: { s1: { turns: [stored(1, '다른 흐름 답')] } } })
    renderEditor()
    await openTab('판단 기록')
    expect(await screen.findByText(/시험 대화를 돌리면/)).toBeInTheDocument()
    expect(fake.calls.some((c) => c.path.includes('/decision-traces'))).toBe(false)
  })
})

describe('캔버스 실시간 노드 상태(#240)', () => {
  const nodeEnd = (id: string, step: number, handle = 'next') => ({ event: 'node_end', data: { node_id: id, kind: 'x', step, handle, duration_ms: 5, input_tokens: 0, output_tokens: 0, cost_usd: null, unpriced_models: [], summary: null } })
  const card = (id: string) => document.querySelector(`[data-node-id="${id}"]`) as HTMLElement

  it('지나간 노드는 끝남이고 호박색 경로로 이어지며 새 대화에서 비워진다', async () => {
    fakeFlowsApi({ graph: GRAPH, testTurns: [[
      { event: 'start', data: { flow_id: 'f1' } },
      { event: 'node_start', data: { node_id: 'start', kind: 'start', step: 0 } }, nodeEnd('start', 0),
      { event: 'node_start', data: { node_id: 'ai1', kind: 'ai_answer', step: 1 } }, nodeEnd('ai1', 1),
      ...turn('답', '0.0001').slice(1),
    ]] })
    renderEditor()
    await openTab('시험 대화')
    await say('안녕')
    await screen.findByText('답')
    await waitFor(() => expect(card('ai1')).toHaveAttribute('data-live', 'done'))
    expect(card('start')).toHaveAttribute('data-live', 'done')
    expect(card('side')).not.toHaveAttribute('data-live')
    // 턴이 끝났으니 시험 대화 탭에서 호박색 경로로 이어진다
    expect(card('ai1')).toHaveAttribute('data-active', 'true')
    await userEvent.click(screen.getByRole('button', { name: '새 대화' }))
    await waitFor(() => expect(card('ai1')).not.toHaveAttribute('data-live'))
  })

  it('오류가 난 노드는 턴이 끝난 뒤에도 오류로 남는다(trace 없음)', async () => {
    fakeFlowsApi({ graph: GRAPH, testTurns: [[
      { event: 'start', data: { flow_id: 'f1' } },
      { event: 'node_start', data: { node_id: 'start', kind: 'start', step: 0 } }, nodeEnd('start', 0),
      { event: 'node_start', data: { node_id: 'ai1', kind: 'ai_answer', step: 1 } },
      { event: 'node_error', data: { node_id: 'ai1', kind: 'ai_answer', step: 1, message: '모델 호출 실패' } },
      { event: 'done', data: { ran: false, code: 'turn_error', note: '', errors: [], session_id: 's1' } },
    ]] })
    renderEditor()
    await openTab('시험 대화')
    await say('안녕')
    await waitFor(() => expect(card('ai1')).toHaveAttribute('data-live', 'error'))
    expect(card('start')).toHaveAttribute('data-live', 'done')
    expect(card('ai1').querySelector('[data-live-mark="error"]')).toHaveAttribute('aria-label', 'N-02 오류')
  })
})
