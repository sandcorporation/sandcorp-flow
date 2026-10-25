// #157: 캔버스 색 모드는 OS(prefers-color-scheme)가 아니라 앱 테마(html.dark)를 따른다.
import { render, act } from '@testing-library/react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import FlowCanvas from './FlowCanvas'
import type { FlowGraph } from '../lib/flowGraph'

const GRAPH: FlowGraph = {
  nodes: [{ id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} }],
  edges: [],
}
const noop = () => {}
function renderCanvas() {
  return render(
    <div style={{ width: 400, height: 400 }}>
      <FlowCanvas graph={GRAPH} errors={[]} selectedId={null} readOnly onSelect={noop} onMoved={noop} onConnect={noop} onDelete={noop} onDropKind={noop} />
    </div>,
  )
}
/** OS 가 다크/라이트라고 답하는 matchMedia. */
function setOsScheme(dark: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /prefers-color-scheme:\s*dark/.test(q) ? dark : false, media: q,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
  }))
}
const flow = (c: HTMLElement) => c.querySelector('.react-flow') as HTMLElement
afterEach(() => { vi.unstubAllGlobals(); document.documentElement.classList.remove('dark') })

describe('캔버스 테마(#157)', () => {
  it('OS 다크 + 앱 라이트 → 캔버스 라이트', () => {
    setOsScheme(true)
    const { container } = renderCanvas()
    expect(flow(container)).toHaveClass('light')
    expect(flow(container)).not.toHaveClass('dark')
  })
  it('OS 라이트 + 앱 다크 → 캔버스 다크', () => {
    setOsScheme(false)
    document.documentElement.classList.add('dark')
    const { container } = renderCanvas()
    expect(flow(container)).toHaveClass('dark')
  })
  it('테마 버튼처럼 html.dark 를 바꾸면 즉시 따라간다', async () => {
    setOsScheme(false)
    const { container } = renderCanvas()
    expect(flow(container)).toHaveClass('light')
    await act(async () => { document.documentElement.classList.add('dark') })
    expect(flow(container)).toHaveClass('dark')
    await act(async () => { document.documentElement.classList.remove('dark') })
    expect(flow(container)).toHaveClass('light')
  })
})
