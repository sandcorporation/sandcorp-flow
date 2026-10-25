import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import TryFlowPage from './TryFlowPage'
import { fakeFlowsApi } from '../flowsFakeApi'
import type { FlowGraph } from '../lib/flowGraph'

// 체험 화면(#37): 편집기를 그대로 쓰되 체험 모드에서는 /api/try 경로·쿠키·배포/버전 UI 숨김.
const GRAPH: FlowGraph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} },
    { id: 'ai1', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 0 }, config: { instructions: '친절하게' } },
  ],
  edges: [{ from: 'start', to: 'ai1' }],
}
const SESSION = { flow_id: 'f1', template: 'shop_support', turns_remaining: 18 }
const TEMPLATES = [{ id: 'shop_support', name: '쇼핑몰 고객 지원', documents: ['환불 규정'] }]
const ent = (o: object) => ({ node_id: 'start', kind: 'start', handle: 'next', record: {}, duration_ms: 2, input_tokens: 0, output_tokens: 0, cost_usd: null, calls: [], ...o })
const ranTurn = (reply: string) => [
  { event: 'start', data: { flow_id: 'f1' } },
  { event: 'trace', data: ent({}) },
  { event: 'trace', data: ent({ node_id: 'ai1', kind: 'ai_answer', input_tokens: 10, output_tokens: 5, record: { reason: '근거를 찾아 답함', output: reply } }) },
  { event: 'reply', data: { text: reply } },
  { event: 'done', data: { ran: true, handoff: false, note: '', errors: [], session_id: 's1', code: null } },
]
const refused = (code: string, note: string) => [
  { event: 'start', data: { flow_id: 'f1' } },
  { event: 'done', data: { ran: false, handoff: false, note, errors: [], session_id: null, code } },
]

function Where() { const l = useLocation(); return <p data-testid="where">{l.pathname}{l.search}</p> }
function renderTry(path = '/try/flow/f1') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route path="/try" element={<p>템플릿 고르기 화면</p>} />
        <Route path="/try/flow/:id" element={<TryFlowPage />} />
        <Route path="/try/view/:id" element={<TryFlowPage view />} />
      </Routes>
    </MemoryRouter>,
  )
}
const aside = () => screen.findByRole('complementary', { name: '노드 설정' })
async function openTab(name: string) { await userEvent.click(await within(await aside()).findByRole('tab', { name })) }
async function say(text: string) {
  await userEvent.type(await screen.findByLabelText('시험 메시지'), text)
  await userEvent.click(screen.getByRole('button', { name: '보내기' }))
}
afterEach(() => { vi.unstubAllGlobals() })

describe('TryFlowPage — 편집기 재사용(체험 모드)', () => {
  it('/api/try 경로로 사본을 읽고 쿠키(same-origin)로 보내며 Authorization 은 싣지 않는다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: SESSION } })
    renderTry()
    expect(await screen.findByText('N-02')).toBeInTheDocument()
    const paths = fake.calls.map((c) => c.path)
    expect(paths).toContain('/api/try/flows/f1/')
    expect(paths).toContain('/api/try/node-schema/')
    expect(paths.some((p) => p.startsWith('/api/tenant/'))).toBe(false)
    for (const c of fake.calls) {
      expect(new Headers(c.init?.headers).get('Authorization')).toBeNull()
      expect(c.init?.credentials).toBe('include')
    }
  })

  it('편집은 /api/try 초안 PATCH 로 저장한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { session: SESSION } })
    renderTry()
    fireEvent.click((await screen.findAllByText('답변')).map((el) => el.closest('[data-node-id]')).find(Boolean)!)
    const name = await screen.findByLabelText('이름')
    await userEvent.clear(name)
    await userEvent.type(name, '새 답변')
    fireEvent.blur(name)
    await waitFor(() => expect(fake.calls.some((c) => c.method === 'PATCH' && c.path === '/api/try/flows/f1/draft/')).toBe(true))
  })

  it('버전 저장·배포·되돌리기 UI 는 숨기고 가입 뒤에 열린다고 알린다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { session: SESSION } })
    renderTry()
    await screen.findByText('N-02')
    expect(screen.queryByText('버전 이력')).toBeNull()
    expect(screen.queryByRole('button', { name: /배포/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /되돌리기/ })).toBeNull()
    expect(screen.getByText(/배포·버전 저장은 가입한 뒤/)).toBeInTheDocument()
  })

  it('남은 턴을 보이고 「이 흐름으로 가입」은 옮기기 로그인으로 보낸다(#42)', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { session: SESSION } })
    renderTry()
    expect(await screen.findByText(/남은 대화 18턴/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '이 흐름으로 가입' })).toHaveAttribute('href', '/api/auth/authorize/member?then=try_claim')
  })

  it('시험 대화는 /api/try …/test/ 로 돌고 판단 기록을 그린다 — 저장된 판단 기록 API 는 부르지 않는다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { session: SESSION }, testTurns: [ranTurn('환불은 7일 이내예요')] })
    renderTry()
    await openTab('시험 대화')
    await say('환불해줘')
    expect(await screen.findByText('환불은 7일 이내예요')).toBeInTheDocument()
    const t = fake.calls.find((c) => c.method === 'POST' && c.path.endsWith('/test/'))!
    expect(t.path).toBe('/api/try/flows/f1/test/')
    expect(new Headers(t.init?.headers).get('Authorization')).toBeNull()
    await openTab('판단 기록')
    expect(await screen.findByText('근거를 찾아 답함')).toBeInTheDocument()
    expect(fake.calls.some((c) => c.path.includes('/decision-traces') || c.path.includes('/api/tenant/'))).toBe(false)
  })

  it('턴이 끝나면 남은 턴을 다시 읽는다', async () => {
    let left = 18
    const fake = fakeFlowsApi({ graph: GRAPH, try: { session: () => ({ ...SESSION, turns_remaining: left }) }, testTurns: [ranTurn('답')] })
    renderTry()
    await screen.findByText(/남은 대화 18턴/)
    left = 17
    await openTab('시험 대화')
    await say('안녕')
    expect(await screen.findByText(/남은 대화 17턴/)).toBeInTheDocument()
    expect(fake.calls.filter((c) => c.path === '/api/try/session/').length).toBeGreaterThanOrEqual(2)
  })

  it('try_daily_budget 이면 「내일 다시」를 알리고 편집은 계속된다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { session: SESSION }, testTurns: [refused('try_daily_budget', '오늘 체험 대화가 모두 찼어요. 내일 다시 찾아 주세요. 편집은 계속할 수 있어요.')] })
    renderTry()
    await openTab('시험 대화')
    await say('안녕')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('내일 다시')
    expect(screen.getByLabelText('시험 메시지')).toBeEnabled() // 막지 않는다 — 편집은 물론 재시도도 서버가 가른다
    expect(screen.getByText('N-02')).toBeInTheDocument()
  })

  it('try_turns_exhausted 이면 가입을 권하고 입력을 막는다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { session: { ...SESSION, turns_remaining: 0 } }, testTurns: [refused('try_turns_exhausted', '이 체험의 대화 턴을 다 썼어요.')] })
    renderTry()
    await openTab('시험 대화')
    await say('안녕')
    expect(await screen.findByText(/가입하면 내 흐름으로 계속 시험/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getByLabelText('시험 메시지')).toBeDisabled())
  })

  it('try_unavailable 이면 준비 중이라고 알린다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { session: SESSION }, testTurns: [refused('try_unavailable', '지금은 체험 대화를 준비하고 있어요. 잠시 후 다시 찾아 주세요.')] })
    renderTry()
    await openTab('시험 대화')
    await say('안녕')
    expect(await screen.findByText(/체험 대화를 준비하고 있어요/)).toBeInTheDocument()
  })

  it('세션이 없으면(401 try_no_session) 템플릿 고르기로 돌려보낸다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { session: null } })
    renderTry()
    expect(await screen.findByText('템플릿 고르기 화면')).toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent('/try?expired=1')
  })
})

describe('TryFlowPage — 흐름 보기(읽기 전용)', () => {
  it('세션 없이 템플릿 그래프를 읽기 전용으로 보여 주고 시험 대화·편집·가입 도움은 없다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderTry('/try/view/shop_support')
    expect((await screen.findAllByText('N-02')).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: '쇼핑몰 고객 지원' })).toBeInTheDocument()
    expect(fake.calls.map((c) => c.path)).toContain('/api/try/templates/shop_support/')
    expect(fake.calls.some((c) => c.path.includes('/flows/'))).toBe(false)
    const tabs = within(await aside()).getAllByRole('tab').map((t) => t.textContent)
    expect(tabs).not.toContain('시험 대화')
    expect(screen.getByText(/읽기 전용/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /보내기/ })).toBeNull()
    expect(screen.queryByRole('link', { name: '이 흐름으로 가입' })).toBeNull() // 사본이 없다 — 「고쳐 보기」 뒤에 가입
    expect(screen.queryByTestId('where')?.textContent).toBe('/try/view/shop_support')
  })
})
