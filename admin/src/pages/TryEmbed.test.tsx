import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import TryPage from './TryPage'
import TryFlowPage from './TryFlowPage'
import { fakeFlowsApi } from '../flowsFakeApi'
import { resetEmbedForTest } from '../lib/embed'
import type { FlowGraph } from '../lib/flowGraph'

// 임베드 모드(#180): portfolio 가 iframe 으로 넣는 /try?embed=1.
const GRAPH: FlowGraph = {
  nodes: [{ id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} }],
  edges: [],
}
const TEMPLATES = [
  { id: 'sandcorp_home', name: 'Sandcorp 홈 상담', documents: ['회사 소개'] },
  { id: 'shop_support', name: '쇼핑몰 고객 지원', documents: [] },
]
const SESSION = { flow_id: 'f1', template: 'sandcorp_home', turns_remaining: 18 }
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
function setWidth(px: number) {
  vi.stubGlobal('matchMedia', (q: string) => {
    const max = Number(/max-width:\s*(\d+)px/.exec(q)?.[1] ?? NaN)
    return { matches: px <= max, media: q, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {} }
  })
}
beforeEach(() => resetEmbedForTest())
afterEach(() => { vi.unstubAllGlobals() })

describe('임베드 — 템플릿 고르기', () => {
  it('머리줄(브랜드 링크·테마 토글)이 없고 일반 방문에는 있다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    const { unmount } = renderAt('/try')
    await screen.findByRole('heading', { name: 'Sandcorp 홈 상담' })
    expect(screen.getByRole('banner')).toBeInTheDocument()
    unmount(); resetEmbedForTest()
    renderAt('/try?embed=1')
    await screen.findByRole('heading', { name: 'Sandcorp 홈 상담' })
    expect(screen.queryByRole('banner')).toBeNull()
    expect(screen.queryByText('Sandcorp Flow 체험')).toBeNull()
  })

  it('template=home 이면 홈 편집기가 곧바로 열리고 세션을 만들지 않는다(#195 — 「고쳐 보기 시작」 없음)', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderAt('/try?embed=1&template=home')
    expect(await screen.findByText('N-01')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /고쳐 보기/ })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Sandcorp Flow 체험' })).toBeNull()
    await waitFor(async () => expect((await fake.statuses()).some((c) => c.path === '/api/try/session/')).toBe(true))
    expect(fake.calls.filter((c) => c.method === 'POST' && c.path === '/api/try/sessions/')).toHaveLength(0)
  })

  it('쿼리 없는 임베드는 템플릿 고르기 그대로다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    renderAt('/try?embed=1')
    await screen.findByRole('heading', { name: 'Sandcorp 홈 상담' })
    expect(screen.getAllByRole('button', { name: '고쳐 보기' })).toHaveLength(2)
  })
})

describe('임베드 — 체험 편집기', () => {
  it('SPA 이동으로 쿼리가 사라져도 임베드가 이어진다: 머리줄 없음, 「이 흐름으로 가입」은 새 탭', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: SESSION } })
    renderAt('/try?embed=1')
    await userEvent.click(await screen.findByRole('link', { name: /이어서 고쳐 보기/ }))
    const signup = await screen.findByRole('link', { name: '이 흐름으로 가입' })
    expect(screen.getByTestId('where')).toHaveTextContent('/try/flow/f1')
    expect(screen.getByTestId('where')).not.toHaveTextContent('embed')
    expect(screen.queryByRole('banner')).toBeNull()
    expect(signup).toHaveAttribute('target', '_blank')
    expect(signup.getAttribute('rel')).toContain('noopener')
    expect(await screen.findByText(/남은 대화 18턴/)).toBeInTheDocument()
  })

  it('임베드가 아니면 「이 흐름으로 가입」은 같은 탭이고 머리줄이 있다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: SESSION } })
    renderAt('/try/flow/f1')
    const signup = await screen.findByRole('link', { name: '이 흐름으로 가입' })
    expect(signup).not.toHaveAttribute('target')
    expect(screen.getByRole('banner')).toBeInTheDocument()
  })

  it('폰 폭: 캔버스 높이는 뷰포트(100dvh)가 아니라 고정 높이다 — iframe 높이와 순환하지 않는다', async () => {
    setWidth(390)
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: SESSION } })
    renderAt('/try/flow/f1?embed=1')
    const canvas = await screen.findByTestId('flow-canvas')
    expect(canvas.className).not.toMatch(/dvh|vh/)
    expect(canvas.className).toMatch(/h-\[\d+px\]/)
    expect(document.querySelector('.min-h-screen, .h-screen, .md\\:h-screen')).toBeNull()
  })

  it('넓은 폭에서도 편집기 격자는 고정 높이다(뷰포트 비례 아님)', async () => {
    setWidth(1280)
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: SESSION } })
    renderAt('/try/flow/f1?embed=1')
    await screen.findByTestId('flow-canvas')
    expect(document.querySelector('.min-h-screen, .h-screen, .md\\:h-screen')).toBeNull()
    expect(document.querySelector('[data-embed-grid]')?.className).toMatch(/lg:h-\[\d+px\]/)
  })
})
