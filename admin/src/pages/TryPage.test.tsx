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
      </Routes>
    </MemoryRouter>,
  )
}
afterEach(() => { vi.unstubAllGlobals() })

describe('TryPage — 템플릿 고르기', () => {
  it('템플릿 셋을 카드로 보이고 각 카드에 「고쳐 보기」만 있다(「흐름 보기」는 없앴다, #199)', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Sandcorp 홈 상담' })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: '고쳐 보기' })).toHaveLength(3)
    expect(screen.queryByRole('link', { name: '흐름 보기' })).toBeNull()
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

  it('카드 「고쳐 보기」는 이동만 한다 — ?template= 편집기로 가고 POST 는 없다(#199)', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderPage()
    const link = (await screen.findAllByRole('link', { name: '고쳐 보기' }))[1]
    expect(link).toHaveAttribute('href', '/try?template=shop')
    await userEvent.click(link)
    expect(await screen.findByRole('heading', { name: '쇼핑몰 고객 지원' })).toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent('/try')
    expect(fake.calls.filter((c) => c.method === 'POST')).toHaveLength(0)
  })

  it('?template=home 같은 짧은 이름은 카드 없이 sandcorp_home 편집기를 곧바로 연다(#195)', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderPage('/try?template=home')
    await waitFor(() => expect(fake.calls.some((c) => c.path.endsWith('/api/try/templates/sandcorp_home/'))).toBe(true))
    expect(await screen.findByRole('heading', { name: 'Sandcorp 홈 상담' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '쇼핑몰 고객 지원' })).toBeNull()
    expect(fake.calls.some((c) => c.path.endsWith('/api/try/templates/'))).toBe(false)
  })

  it('다른 템플릿의 체험이 진행 중이면 편집기에서 「새 체험으로 바뀝니다」를 알리고 세션은 만들지 않는다(#197)', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: { flow_id: 'f7', template: 'shop_support', turns_remaining: 12 } } })
    renderPage('/try?template=home')
    expect(await screen.findByTestId('try-notice')).toHaveTextContent('새 체험으로 바뀝니다')
    expect(fake.calls.filter((c) => c.method === 'POST')).toHaveLength(0)
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
    await waitFor(() => expect(screen.getAllByRole('link', { name: '고쳐 보기' })).toHaveLength(3))
  })
})
