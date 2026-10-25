import { render, screen, fireEvent, within } from '@testing-library/react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import PublicFlowPage from './PublicFlowPage'
import { fakeFlowsApi } from '../flowsFakeApi'
import type { FlowGraph } from '../lib/flowGraph'

// 공개 흐름 보기(#39): 로그인 없이 테넌트의 공개 흐름(배포 버전)을 읽기 전용으로 본다.
const GRAPH: FlowGraph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} },
    { id: 'ai1', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 0 }, config: { instructions: '친절하게', await_reply: false } },
    { id: 'api', kind: 'http_tool', code: 'N-03', name: '주문 조회', position: { x: 520, y: 0 }, config: { method: 'GET' } },
  ],
  edges: [{ from: 'start', to: 'ai1' }, { from: 'ai1', to: 'api' }],
}
const HEAD = { brand_name: 'Sandcorp', flow_name: 'Sandcorp 홈 상담', version: 3, template: 'sandcorp_home' }

function setWidth(px: number) {
  vi.stubGlobal('matchMedia', (q: string) => {
    const max = Number(/max-width:\s*(\d+)px/.exec(q)?.[1] ?? NaN)
    return { matches: px <= max, media: q, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {} }
  })
}
function renderPublic(slug = 'sandcorp') {
  return render(
    <MemoryRouter initialEntries={[`/flows/${slug}`]}>
      <Routes><Route path="/flows/:id" element={<PublicFlowPage />} /></Routes>
    </MemoryRouter>,
  )
}
afterEach(() => { vi.unstubAllGlobals() })

describe('PublicFlowPage — 공개 흐름 보기', () => {
  it('익명으로 공개 API 만 읽고 배포 버전을 읽기 전용으로 보인다(저장·시험 대화·판단 기록·버전 없음)', async () => {
    setWidth(1280)
    const fake = fakeFlowsApi({ graph: GRAPH, public: { sandcorp: HEAD } })
    const { container } = renderPublic()
    expect((await screen.findAllByText('N-02')).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'Sandcorp 홈 상담' })).toBeInTheDocument()
    expect(screen.getByText(/Sandcorp · 배포 중인 v3/)).toBeInTheDocument()
    const paths = fake.calls.map((c) => c.path)
    expect(paths).toContain('/api/public/flows/sandcorp/')
    expect(paths.some((p) => p.startsWith('/api/tenant/') || p.includes('/api/try/flows/') || p.includes('/api/try/session'))).toBe(false)
    const pub = fake.calls.find((c) => c.path === '/api/public/flows/sandcorp/')!
    expect(new Headers(pub.init?.headers).get('Authorization')).toBeNull()
    expect(container.querySelectorAll('.react-flow__node.draggable').length).toBe(0) // 넓은 폭에서도 끌기 없음
    expect(container.querySelectorAll('.react-flow__handle.connectable').length).toBe(0)
    expect(screen.queryByRole('button', { name: 'AI 답변' })).toBeNull() // 팔레트 없음
    const tabs = within(screen.getByRole('complementary', { name: '노드 설정' })).getAllByRole('tab').map((t) => t.textContent)
    expect(tabs).not.toContain('시험 대화')
    expect(tabs).not.toContain('판단 기록')
    expect(screen.queryByRole('button', { name: /저장|배포/ })).toBeNull()
    expect(screen.getByText(/비밀·내부 주소·헤더·본문은 숨김/)).toBeInTheDocument()
    expect(screen.queryByText('초안 저장됨')).toBeNull() // 초안이 없는 보기
  })

  it('예제 그대로인 흐름이면 「고쳐 보기」가 /try?template=home 으로 간다', async () => {
    setWidth(1280)
    fakeFlowsApi({ graph: GRAPH, public: { sandcorp: HEAD } })
    renderPublic()
    expect(await screen.findByRole('link', { name: '고쳐 보기' })).toHaveAttribute('href', '/try?template=home')
  })

  it('예제와 다른 흐름이면 「고쳐 보기」가 없다', async () => {
    setWidth(1280)
    fakeFlowsApi({ graph: GRAPH, public: { shop: { ...HEAD, template: null } } })
    renderPublic('shop')
    await screen.findAllByText('N-02')
    expect(screen.queryByRole('link', { name: '고쳐 보기' })).toBeNull()
  })

  it('없거나 공개 보기를 끈 테넌트는 같은 「공개된 흐름이 없습니다」', async () => {
    setWidth(1280)
    fakeFlowsApi({ graph: GRAPH, public: {} })
    renderPublic('nope')
    expect(await screen.findByText('공개된 흐름이 없습니다.')).toBeInTheDocument()
    expect(screen.queryByText('N-01')).toBeNull()
  })

  it('노드를 누르면 공개된 설정만 읽기 전용으로 보인다', async () => {
    setWidth(1280)
    fakeFlowsApi({ graph: GRAPH, public: { sandcorp: HEAD } })
    renderPublic()
    fireEvent.click((await screen.findAllByText('N-02'))[0])
    const aside = screen.getByRole('complementary', { name: '노드 설정' })
    expect(await within(aside).findByDisplayValue('친절하게')).toHaveAttribute('readonly')
    expect(within(aside).queryByRole('button', { name: /노드 삭제/ })).toBeNull()
  })

  it('HTTP 도구 노드를 눌러도 테넌트 API(비밀·허용 도메인)를 부르지 않는다', async () => {
    setWidth(1280)
    const fake = fakeFlowsApi({ graph: GRAPH, public: { sandcorp: HEAD } })
    renderPublic()
    fireEvent.click((await screen.findAllByText('N-03'))[0])
    await screen.findByDisplayValue('주문 조회')
    await new Promise((r) => setTimeout(r, 20))
    expect(fake.calls.some((c) => c.path.startsWith('/api/tenant/'))).toBe(false)
  })

  it('폰 폭: 노드를 누르면 읽기 전용 시트, 하단 탭은 노드 설정 하나', async () => {
    setWidth(390)
    fakeFlowsApi({ graph: GRAPH, public: { sandcorp: HEAD } })
    renderPublic()
    fireEvent.click((await screen.findAllByText('N-02'))[0])
    const sheet = await screen.findByRole('dialog', { name: '노드 설정' })
    expect(within(sheet).getByLabelText('이름')).toHaveAttribute('readonly')
    const nav = screen.getByRole('navigation', { name: '하단 탭' })
    expect(within(nav).getAllByRole('button').map((b) => b.textContent)).toEqual(['노드 설정'])
  })
})
