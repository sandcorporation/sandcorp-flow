import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import TrySignupPage from './TrySignupPage'
import { fakeFlowsApi } from '../flowsFakeApi'
import type { FlowGraph } from '../lib/flowGraph'

// 「이 흐름으로 가입」 중간 페이지(#198, ADR-0032 Q4): 세션이 없으면 만들고, 있으면 그대로, 어느 쪽이든 옮기기 로그인으로 간다.
const GRAPH: FlowGraph = {
  nodes: [{ id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} }],
  edges: [],
}
const TEMPLATES = [{ id: 'sandcorp_home', name: 'Sandcorp 홈 상담', documents: [] }]
const SESSION = { flow_id: 'f1', template: 'sandcorp_home', turns_remaining: 20 }
const posts = (fake: ReturnType<typeof fakeFlowsApi>) => fake.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/api/try/sessions/'))

let replace: ReturnType<typeof vi.fn>
const original = window.location
beforeEach(() => {
  replace = vi.fn()
  Object.defineProperty(window, 'location', { configurable: true, value: { ...original, replace } })
  sessionStorage.clear()
})
afterEach(() => { Object.defineProperty(window, 'location', { configurable: true, value: original }) })

function renderSignup(path = '/try/signup?template=home') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/try/signup" element={<TrySignupPage />} />
        <Route path="/try" element={<p>체험 시작</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('TrySignupPage', () => {
  it('세션이 없으면 POST 한 번으로 만든 뒤 옮기기 로그인으로 location.replace 한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderSignup()
    await waitFor(() => expect(replace).toHaveBeenCalledWith(expect.stringContaining('then=try_claim')))
    expect(posts(fake)).toHaveLength(1)
    expect(posts(fake)[0].body.template).toBe('sandcorp_home')
    expect(posts(fake)[0].body.graph ?? null).toBeNull() // 보관된 지역 편집이 없으면 템플릿 그대로
  })

  it('이 탭에 보관된 지역 그래프가 있으면 그것을 graph 로 실어 세션을 만든다', async () => {
    const edited = { ...GRAPH, nodes: [{ ...GRAPH.nodes[0], name: '고친 시작' }] }
    sessionStorage.setItem('sandcorp-flow:try-draft:sandcorp_home', JSON.stringify(edited))
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderSignup()
    await waitFor(() => expect(replace).toHaveBeenCalled())
    expect((posts(fake)[0].body.graph as FlowGraph).nodes[0].name).toBe('고친 시작')
  })

  it('세션이 이미 있으면 POST 없이 바로 간다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: SESSION } })
    renderSignup()
    await waitFor(() => expect(replace).toHaveBeenCalledWith(expect.stringContaining('then=try_claim')))
    expect(posts(fake)).toHaveLength(0)
  })

  it('거절되면 사유와 「체험으로 돌아가기」를 보이고 이동하지 않는다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null, startStatus: 429 } })
    renderSignup()
    expect(await screen.findByText(/오늘 이 네트워크에서/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '체험으로 돌아가기' }).getAttribute('href')).toBe('/try?template=home')
    expect(replace).not.toHaveBeenCalled()
  })

  it('색인 제외: robots noindex,nofollow 메타를 넣고 떠나면 걷는다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: SESSION } })
    const { unmount } = renderSignup()
    expect(document.querySelector('meta[name="robots"]')?.getAttribute('content')).toBe('noindex,nofollow')
    unmount()
    expect(document.querySelector('meta[name="robots"]')).toBeNull()
  })

  it('template 이 없으면 체험 시작으로 보낸다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderSignup('/try/signup')
    expect(await screen.findByText('체험 시작')).toBeInTheDocument()
  })
})
