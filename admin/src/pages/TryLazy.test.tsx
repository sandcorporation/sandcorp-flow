import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import TryPage from './TryPage'
import TryFlowPage from './TryFlowPage'
import { fakeFlowsApi } from '../flowsFakeApi'
import { resetEmbedForTest } from '../lib/embed'
import type { FlowGraph } from '../lib/flowGraph'

// 방문 즉시 편집기(#195, ADR-0032 초안): /try?template=… 는 카드 없이 곧바로 편집기를 열고, 세션은 첫 쓰기에 만든다.
const GRAPH: FlowGraph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} },
    { id: 'ai1', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 0 }, config: { instructions: '친절하게' } },
  ],
  edges: [{ from: 'start', to: 'ai1' }],
}
const TEMPLATES = [
  { id: 'sandcorp_home', name: 'Sandcorp 홈 상담', documents: ['회사 소개'] },
  { id: 'shop_support', name: '쇼핑몰 고객 지원', documents: [] },
]
const RAN = [
  { event: 'start', data: { flow_id: 'f1' } },
  { event: 'reply', data: { text: '안녕하세요' } },
  { event: 'done', data: { ran: true, handoff: false, note: '', errors: [], session_id: 's1', code: null } },
]

function Where() { const l = useLocation(); return <p data-testid="where">{l.pathname}{l.search}</p> }
function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route path="/try" element={<TryPage />} />
        <Route path="/try/flow/:id" element={<TryFlowPage />} />
      </Routes>
    </MemoryRouter>,
  )
}
const posts = (fake: ReturnType<typeof fakeFlowsApi>) => fake.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/api/try/sessions/'))
async function rename(nodeName: string, to: string) {
  fireEvent.click((await screen.findAllByText(nodeName)).map((el) => el.closest('[data-node-id]')).find(Boolean)!)
  const name = await within(await screen.findByRole('complementary', { name: '노드 설정' })).findByLabelText('이름')
  await userEvent.clear(name)
  await userEvent.type(name, to)
  fireEvent.blur(name)
}
beforeEach(() => resetEmbedForTest())
afterEach(() => { vi.unstubAllGlobals() })

describe('방문 즉시 편집기 — 진입은 아무것도 만들지 않는다', () => {
  it('?template=home 이면 카드 없이 홈 템플릿 편집기가 뜨고, 템플릿·노드 틀·세션 읽기 세 가지만 부른다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderAt('/try?template=home')
    expect(await screen.findByText('N-02')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /고쳐 보기/ })).toBeNull()
    await waitFor(() => expect(fake.calls.some((c) => c.path.endsWith('/api/try/node-schema/'))).toBe(true))
    const paths = new Set(fake.calls.map((c) => `${c.method} ${c.path.replace(/^.*\/api\//, '/api/')}`))
    expect(paths).toEqual(new Set(['GET /api/try/templates/sandcorp_home/', 'GET /api/try/node-schema/', 'GET /api/try/session/']))
    expect(posts(fake)).toHaveLength(0)
    expect(screen.getByTestId('where')).toHaveTextContent('/try?template=home')
  })

  it('무세션 편집기는 4xx 응답을 받지 않는다(401 try_no_session 없음)', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderAt('/try?template=home')
    expect(await screen.findByText('N-02')).toBeInTheDocument()
    await waitFor(() => expect(fake.calls.length).toBeGreaterThanOrEqual(3))
    expect((await fake.statuses()).filter((s) => s.status >= 400)).toEqual([])
  })

  it('세션이 있고 같은 템플릿이면 그 사본을 연다 — 주소는 그대로, 세션을 새로 만들지 않는다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: { flow_id: 'f1', template: 'sandcorp_home', turns_remaining: 17 } } })
    renderAt('/try?template=home')
    expect(await screen.findByText('N-02')).toBeInTheDocument()
    expect(fake.calls.some((c) => c.method === 'GET' && c.path.endsWith('/api/try/flows/f1/'))).toBe(true)
    expect(await screen.findByText(/남은 대화 17턴/)).toBeInTheDocument()
    expect(posts(fake)).toHaveLength(0)
    expect(screen.getByTestId('where')).toHaveTextContent('/try?template=home')
  })

  it('세션이 다른 템플릿 것이면 그 사본이 아니라 이 템플릿을 연다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: { flow_id: 'f9', template: 'shop_support', turns_remaining: 17 } } })
    renderAt('/try?template=home')
    expect(await screen.findByText('N-02')).toBeInTheDocument()
    expect(fake.calls.some((c) => c.path.endsWith('/api/try/templates/sandcorp_home/'))).toBe(true)
    expect(fake.calls.some((c) => c.path.includes('/api/try/flows/'))).toBe(false)
  })

  it('모르는 템플릿이면 카드 고르기로 내려앉고 그렇다고 알린다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderAt('/try?template=nope')
    expect(await screen.findByText(/템플릿을 찾지 못했/)).toBeInTheDocument()
    expect(await screen.findByText('쇼핑몰 고객 지원')).toBeInTheDocument()
  })
})

describe('방문 즉시 편집기 — 첫 쓰기에 세션', () => {
  it('첫 노드 설정 변경이 세션을 한 번 만들고 그 연산을 새 사본 초안에 PATCH 한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderAt('/try?template=home')
    await rename('답변', '새 답변')
    await waitFor(() => expect(fake.patches()).toHaveLength(1))
    expect(posts(fake)).toHaveLength(1)
    expect(posts(fake)[0].body).toEqual({ template: 'sandcorp_home' })
    const patch = fake.calls.find((c) => c.method === 'PATCH')!
    expect(patch.path).toMatch(/\/api\/try\/flows\/f1\/draft\/$/)
    expect(patch.body.ops).toEqual([{ op: 'upsert_node', id: 'ai1', name: '새 답변' }])
    expect(await screen.findByText('초안 저장됨')).toBeInTheDocument()
    expect(await screen.findByText(/남은 대화 20턴/)).toBeInTheDocument()
  })

  it('세션을 만드는 동안 편집이 더 들어와도 POST 는 한 번이고 두 편집 모두 순서대로 저장된다', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null, startGate: () => gate } })
    renderAt('/try?template=home')
    await rename('답변', '첫째')
    await waitFor(() => expect(posts(fake)).toHaveLength(1))
    await rename('시작', '둘째')
    expect(screen.getByText('저장 중')).toBeInTheDocument()
    release()
    await waitFor(() => expect(fake.patches()).toHaveLength(2))
    expect(posts(fake)).toHaveLength(1)
    expect(fake.patches()).toEqual([[{ op: 'upsert_node', id: 'ai1', name: '첫째' }], [{ op: 'upsert_node', id: 'start', name: '둘째' }]])
    expect(await screen.findByText('초안 저장됨')).toBeInTheDocument()
    expect(screen.getAllByText('첫째').length).toBeGreaterThan(0)
    expect(screen.getAllByText('둘째').length).toBeGreaterThan(0)
  })

  it('세션 없이 시험 대화를 보내면 세션을 만든 뒤 그 사본으로 턴을 돈다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, testTurns: [RAN], try: { templates: TEMPLATES, session: null } })
    renderAt('/try?template=home')
    await userEvent.click(await within(await screen.findByRole('complementary', { name: '노드 설정' })).findByRole('tab', { name: '시험 대화' }))
    await userEvent.type(await screen.findByLabelText('시험 메시지'), '안녕')
    await userEvent.click(screen.getByRole('button', { name: '보내기' }))
    expect(await screen.findByText('안녕하세요')).toBeInTheDocument()
    expect(posts(fake)).toHaveLength(1)
    const iPost = fake.calls.findIndex((c) => c.method === 'POST' && c.path.endsWith('/api/try/sessions/'))
    const iTest = fake.calls.findIndex((c) => c.method === 'POST' && c.path.endsWith('/api/try/flows/f1/test/'))
    expect(iPost).toBeGreaterThanOrEqual(0)
    expect(iTest).toBeGreaterThan(iPost)
  })

  it('세션이 생긴 뒤 다시 들어오면(새로고침) 같은 사본을 연다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    const first = renderAt('/try?template=home')
    await rename('답변', '새 답변')
    await waitFor(() => expect(fake.patches()).toHaveLength(1))
    first.unmount()
    renderAt('/try?template=home')
    await waitFor(() => expect(fake.calls.filter((c) => c.method === 'GET' && c.path.endsWith('/api/try/flows/f1/'))).toHaveLength(1))
    expect(posts(fake)).toHaveLength(1)
  })
})

describe('방문 즉시 편집기 — 「이 흐름으로 가입」(세션 없을 때 임시 처리, #198 에서 바뀜)', () => {
  it('일반 화면: 세션이 없으면 누를 때 세션을 먼저 만들고 옮기기 로그인으로 간다', async () => {
    const original = window.location
    const assign = vi.fn()
    Object.defineProperty(window, 'location', { configurable: true, value: { ...original, assign } })
    try {
      const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
      renderAt('/try?template=home')
      await userEvent.click(await screen.findByRole('button', { name: '이 흐름으로 가입' }))
      await waitFor(() => expect(assign).toHaveBeenCalledWith(expect.stringContaining('then=try_claim')))
      expect(posts(fake)).toHaveLength(1)
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: original })
    }
  })

  it('임베드: 세션이 없으면 가입 링크를 막고 먼저 고쳐 보라고 안내한다 — 세션을 만들지 않는다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderAt('/try?embed=1&template=home')
    expect(await screen.findByText('N-02')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: '이 흐름으로 가입' })).toBeNull()
    expect(screen.getByText(/고치면 가입/)).toBeInTheDocument()
    expect(posts(fake)).toHaveLength(0)
  })

  it('임베드: 첫 저장으로 세션이 생기면 가입 링크가 새 탭으로 열린다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderAt('/try?embed=1&template=home')
    await rename('답변', '새 답변')
    const link = await screen.findByRole('link', { name: '이 흐름으로 가입' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('href')).toContain('then=try_claim')
  })
})
