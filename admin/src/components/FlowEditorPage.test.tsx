import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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
    { id: 'guard1', kind: 'guardrail', code: 'N-03', name: '검사', position: { x: 520, y: 0 }, config: { rules: ['pii'], action: 'block', target: 'output' } },
    { id: 'human1', kind: 'handoff', code: 'N-04', name: '상담원', position: { x: 780, y: 0 }, config: { reason: '' } },
  ],
  edges: [{ from: 'start', to: 'ai1' }],
}

// d3-drag 가 mousedown 의 event.view 를 읽는데 user-event 는 그것을 채우지 않는다 — 노드 선택은 click 만 보낸다.
async function pick(text: string) { fireEvent.click(await screen.findByText(text)) }

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/tenant/flows/f1']}>
      <Routes><Route path="/tenant/flows/:id" element={<FlowEditorPage />} /></Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { vi.mocked(api.currentAgentRole).mockReturnValue('admin') })
afterEach(() => { vi.unstubAllGlobals() })

describe('FlowEditorPage — 캔버스·노드 목록·초안 저장', () => {
  it('초안의 노드를 N-코드와 이름으로 그린다', async () => {
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    expect(await screen.findByText('N-02')).toBeInTheDocument()
    expect(screen.getByText('답변')).toBeInTheDocument()
    expect(screen.getByText('N-03')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '기본 흐름' })).toBeInTheDocument()
  })

  it('가드레일은 경고띠, 사람 넘김은 적색 모양으로 그린다', async () => {
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await screen.findByText('N-03')
    expect(document.querySelector('[data-node-id="guard1"]')).toHaveAttribute('data-style', 'guard')
    expect(document.querySelector('[data-node-id="human1"]')).toHaveAttribute('data-style', 'human')
    expect(document.querySelector('[data-node-id="ai1"]')).toHaveAttribute('data-style', 'plain')
  })

  it('검증 오류를 해당 노드에 램프와 사유로 보인다', async () => {
    fakeFlowsApi({ graph: GRAPH, errors: [
      { node_id: 'guard1', code: 'invalid_config', message: 'rules 가 비었습니다' },
      { node_id: null, code: 'start_count', message: '시작 노드가 정확히 하나여야 합니다' },
    ] })
    renderEditor()
    const lamp = await screen.findByLabelText('N-03 오류 1건')
    expect(lamp).toHaveAttribute('title', expect.stringContaining('rules 가 비었습니다'))
    expect(screen.queryByLabelText('N-02 오류 1건')).toBeNull()
    expect(screen.getByRole('alert')).toHaveTextContent('시작 노드가 정확히 하나여야 합니다')
  })

  it('노드 목록에서 종류를 누르면 노드를 더하고 upsert_node 로 저장한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await screen.findByText('N-02')
    await userEvent.click(screen.getByRole('button', { name: /AI 분류·추출/ }))
    await waitFor(() => expect(fake.patches()).toHaveLength(1))
    const [op] = fake.patches()[0]
    expect(op).toMatchObject({ op: 'upsert_node', kind: 'ai_classify', code: 'N-05', id: 'ai_classify_1' })
    expect(op.config).toBeTruthy()
    expect(await screen.findByText('N-05')).toBeInTheDocument()
  })

  it('노드를 고르면 오른쪽 패널의 JSON 설정을 고쳐 config 통째로 저장한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('답변')
    const box = await screen.findByLabelText('설정(JSON)')
    expect(box).toHaveValue(JSON.stringify({ instructions: '친절하게' }, null, 2))
    await userEvent.clear(box)
    await userEvent.click(box)
    await userEvent.paste('{"instructions": "짧게", "await_reply": true}')
    await userEvent.click(screen.getByRole('button', { name: '설정 적용' }))
    await waitFor(() => expect(fake.patches()).toHaveLength(1))
    expect(fake.patches()[0]).toEqual([{ op: 'upsert_node', id: 'ai1', config: { instructions: '짧게', await_reply: true } }])
  })

  it('JSON 이 아니면 저장하지 않고 이유를 알려 준다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('답변')
    const box = await screen.findByLabelText('설정(JSON)')
    await userEvent.clear(box)
    await userEvent.click(box)
    await userEvent.paste('{ 깨진')
    await userEvent.click(screen.getByRole('button', { name: '설정 적용' }))
    expect(await screen.findByText(/JSON 이 올바르지 않습니다/)).toBeInTheDocument()
    expect(fake.patches()).toHaveLength(0)
  })

  it('선택한 노드를 지우면 delete_node 로 저장하고 캔버스에서 사라진다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('상담원')
    await userEvent.click(screen.getByRole('button', { name: '노드 삭제' }))
    await waitFor(() => expect(fake.patches()).toEqual([[{ op: 'delete_node', id: 'human1' }]]))
    await waitFor(() => expect(screen.queryByText('N-04')).toBeNull())
  })

  it('저장이 거절되면(422) 사유를 보이고 서버 초안으로 맞추고 알린다', async () => {
    fakeFlowsApi({ graph: GRAPH, patchStatus: 422, patchBody: { detail: "ops[0]: 노드 'x'가 없습니다.", code: 'invalid_patch', index: 0 } })
    renderEditor()
    await pick('상담원')
    await userEvent.click(screen.getByRole('button', { name: '노드 삭제' }))
    expect(await screen.findByText(/저장하지 못했습니다/)).toBeInTheDocument()
  })

  it('flow.edit 권한이 없는 멤버는 읽기 전용이다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('답변')
    expect(screen.queryByRole('button', { name: /AI 분류·추출/ })).toBeNull()
    expect(await screen.findByLabelText('설정(JSON)')).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: '설정 적용' })).toBeNull()
    expect(screen.queryByRole('button', { name: '노드 삭제' })).toBeNull()
    expect(screen.getByText(/읽기 전용/)).toBeInTheDocument()
    expect(fake.patches()).toHaveLength(0)
  })
})
