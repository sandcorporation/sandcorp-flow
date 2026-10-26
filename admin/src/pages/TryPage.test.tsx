import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import TryPage from './TryPage'
import { fakeFlowsApi } from '../flowsFakeApi'

const GRAPH = { nodes: [], edges: [] }
const TEMPLATES = [
  { id: 'sandcorp_home', name: 'Sandcorp 홈 상담', documents: ['회사 소개', '서비스'] },
  { id: 'shop_support', name: '쇼핑몰 고객 지원', documents: ['환불 규정'] },
  { id: 'academy_reservation', name: '학원 예약', documents: [] },
]
function Where() { const l = useLocation(); return <p data-testid="where">{l.pathname}</p> }
function renderPage(path = '/try') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Where />
      <Routes>
        <Route path="/try" element={<TryPage />} />
        <Route path="/try/flow/:id" element={<p>편집기 화면</p>} />
        <Route path="/try/view/:id" element={<p>읽기 전용 화면</p>} />
      </Routes>
    </MemoryRouter>,
  )
}
afterEach(() => { vi.unstubAllGlobals() })

describe('TryPage — 템플릿 고르기', () => {
  it('템플릿 셋을 카드로 보이고 각 카드에 「흐름 보기」「고쳐 보기」가 있다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Sandcorp 홈 상담' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '고쳐 보기' })).toHaveLength(3)
    expect(screen.getAllByRole('link', { name: '흐름 보기' })).toHaveLength(3)
    expect(screen.getByText(/지식 문서 · 환불 규정/)).toBeInTheDocument()
  })

  it('세션이 없으면 4xx 응답 없이 200 null 로 받고 이어서 배너를 보이지 않는다(#163)', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderPage()
    await screen.findByRole('heading', { name: 'Sandcorp 홈 상담' })
    await waitFor(async () => expect((await fake.statuses()).some((c) => c.path === '/api/try/session/')).toBe(true))
    const all = await fake.statuses()
    expect(all.filter((c) => c.status >= 400)).toEqual([])
    expect(all.find((c) => c.path === '/api/try/session/')!.status).toBe(200)
    expect(screen.queryByText(/진행 중인 체험이 있어요/)).not.toBeInTheDocument()
  })

  it('「흐름 보기」는 세션 없이 읽기 전용 화면으로 간다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderPage()
    await userEvent.click((await screen.findAllByRole('link', { name: '흐름 보기' }))[1])
    expect(await screen.findByText('읽기 전용 화면')).toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent('/try/view/shop_support')
  })

  it('「고쳐 보기」는 세션을 만들고(POST sessions) 편집기로 간다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: { flow_id: 'f9', template: 'shop_support', turns_remaining: 20 } } })
    renderPage()
    // 아직 세션이 없다고 가정하기 위해 처음 한 번만 401 — 아래 별도 시험에서 이어 가기를 본다.
    await userEvent.click((await screen.findAllByRole('button', { name: '고쳐 보기' }))[1])
    expect(await screen.findByText('편집기 화면')).toBeInTheDocument()
    const post = fake.calls.find((c) => c.method === 'POST' && c.path === '/api/try/sessions/')!
    expect(post.body).toEqual({ template: 'shop_support' })
    expect(screen.getByTestId('where')).toHaveTextContent('/try/flow/f9')
  })

  it('?template=home 같은 짧은 이름은 카드 없이 sandcorp_home 편집기를 곧바로 연다(#195)', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderPage('/try?template=home')
    await waitFor(() => expect(fake.calls.some((c) => c.path.endsWith('/api/try/templates/sandcorp_home/'))).toBe(true))
    expect(await screen.findByRole('heading', { name: 'Sandcorp 홈 상담' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '쇼핑몰 고객 지원' })).toBeNull()
    expect(fake.calls.some((c) => c.path.endsWith('/api/try/templates/'))).toBe(false)
  })

  it('이 IP가 오늘 3세션을 다 썼으면(429 try_ip_limit) 사유를 그대로 알린다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null, startStatus: 429 } })
    renderPage()
    await userEvent.click((await screen.findAllByRole('button', { name: '고쳐 보기' }))[0])
    expect(await screen.findByRole('alert')).toHaveTextContent('내일 다시 찾아 주세요')
    expect(screen.queryByText('편집기 화면')).toBeNull()
  })

  it('살아 있는 세션이 있으면 남은 턴과 함께 「이어서 고쳐 보기」를 준다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: { flow_id: 'f7', template: 'shop_support', turns_remaining: 12 } } })
    renderPage()
    const link = await screen.findByRole('link', { name: /이어서 고쳐 보기/ })
    expect(link).toHaveAttribute('href', '/try/flow/f7')
    expect(screen.getByText(/남은 대화 12턴/)).toBeInTheDocument()
  })

  it('세션이 만료돼 돌아왔으면(?expired=1) 안내한다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderPage('/try?expired=1')
    expect(await screen.findByText(/체험이 끝났거나 만료/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByRole('button', { name: '고쳐 보기' })).toHaveLength(3))
  })
})
