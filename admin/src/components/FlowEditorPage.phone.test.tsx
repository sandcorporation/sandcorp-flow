import { render, screen, within, fireEvent } from '@testing-library/react'
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
  ],
  edges: [{ from: 'start', to: 'ai1' }],
}

/** matchMedia 를 흉내 낸다 — 폭 px 이 (max-width: N px) 안이면 matches. */
function setWidth(px: number) {
  vi.stubGlobal('matchMedia', (q: string) => {
    const max = Number(/max-width:\s*(\d+)px/.exec(q)?.[1] ?? NaN)
    return { matches: px <= max, media: q, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {} }
  })
}
function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/tenant/flows/f1']}>
      <Routes><Route path="/tenant/flows/:id" element={<FlowEditorPage />} /></Routes>
    </MemoryRouter>,
  )
}
beforeEach(() => { vi.mocked(api.currentAgentRole).mockReturnValue('admin') })
afterEach(() => { vi.unstubAllGlobals() })

describe('편집기 폰 모드(#38)', () => {
  it('넓은 폭: 노드를 끌고 선을 이을 수 있고 팔레트·삭제가 있다', async () => {
    setWidth(1280)
    fakeFlowsApi({ graph: GRAPH })
    const { container } = renderEditor()
    await screen.findByText('N-02')
    expect(container.querySelectorAll('.react-flow__node.draggable').length).toBe(2)
    expect(container.querySelectorAll('.react-flow__handle.connectable').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'AI 답변' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: '하단 탭' })).toBeNull()
  })

  it('좁은 폭: 끌기·연결이 꺼지고 팔레트가 없다 — 팬·줌과 노드 누르기만', async () => {
    setWidth(390)
    fakeFlowsApi({ graph: GRAPH })
    const { container } = renderEditor()
    await screen.findByText('N-02')
    expect(container.querySelectorAll('.react-flow__node.draggable').length).toBe(0)
    expect(container.querySelectorAll('.react-flow__node.selectable').length).toBe(2)
    expect(container.querySelectorAll('.react-flow__handle.connectable').length).toBe(0)
    expect(screen.queryByText(/눌러서 더하거나/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'AI 답변' })).toBeNull()
  })

  it('좁은 폭: 노드를 누르면 설정 시트가 열리고 값 고치기는 되지만 삭제는 없다', async () => {
    setWidth(390)
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    fireEvent.click(await screen.findByText('N-02'))
    const sheet = await screen.findByRole('dialog', { name: '노드 설정' })
    expect(within(sheet).getByLabelText('이름')).not.toHaveAttribute('readonly')
    expect(within(sheet).queryByRole('button', { name: /노드 삭제/ })).toBeNull()
    const name = within(sheet).getByLabelText('이름')
    await userEvent.clear(name)
    await userEvent.type(name, '새이름')
    await userEvent.tab()
    expect(fake.calls.some((c) => c.method === 'PATCH' && JSON.stringify(c.body).includes('새이름'))).toBe(true)
  })

  it('좁은 폭: 하단 탭에서 시험 대화 시트를 연다', async () => {
    setWidth(390)
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    const nav = await screen.findByRole('navigation', { name: '하단 탭' })
    expect(within(nav).getAllByRole('button').map((b) => b.textContent)).toEqual(['노드 설정', '시험 대화', '판단 기록', '버전'])
    await userEvent.click(within(nav).getByRole('button', { name: '시험 대화' }))
    expect(await screen.findByLabelText('시험 메시지')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '시트 닫기' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('768 경계: 767은 폰, 768은 PC 편집', async () => {
    setWidth(768)
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await screen.findByText('N-02')
    expect(screen.queryByRole('navigation', { name: '하단 탭' })).toBeNull()
  })
})
