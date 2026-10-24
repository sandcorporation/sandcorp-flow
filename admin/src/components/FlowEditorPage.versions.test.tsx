import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import * as api from '../api'
import FlowEditorPage from './FlowEditorPage'
import { fakeFlowsApi, type FakeRequest, type FakeVersion } from '../flowsFakeApi'
import type { FlowGraph } from '../lib/flowGraph'

vi.mock('../api', () => ({ currentAgentRole: vi.fn() }))

const node = (id: string, code: string, name: string, x = 0) => ({ id, kind: id === 'start' ? 'start' : 'ai_answer', code, name, position: { x, y: 0 }, config: {} })
const DRAFT: FlowGraph = { nodes: [node('start', 'N-01', '시작'), node('ai1', 'N-02', '새 답변', 260)], edges: [{ from: 'start', to: 'ai1' }] }
const OLD: FlowGraph = { nodes: [node('start', 'N-01', '시작'), node('old1', 'N-02', '옛 답변', 260)], edges: [{ from: 'start', to: 'old1' }] }
const V: FakeVersion[] = [
  { number: 1, note: '첫 배포본', created_by_name: '김관리', created_at: '2026-10-01T09:00:00Z' },
  { number: 2, note: '환불 문구 수정', created_by_name: '박둘째', created_at: '2026-10-02T09:00:00Z' },
]
const REQ = (o: Partial<FakeRequest> = {}): FakeRequest => ({ id: 'r1', flow_id: 'f1', flow_name: '기본 흐름', version: 2, status: 'pending', requested_by_name: '박둘째', decided_by_name: null, decided_at: null, decision_note: '', created_at: '2026-10-02T11:00:00Z', ...o })

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/tenant/flows/f1']}>
      <Routes><Route path="/tenant/flows/:id" element={<FlowEditorPage />} /></Routes>
    </MemoryRouter>,
  )
}
const panel = async () => within(await screen.findByRole('region', { name: '버전 이력' }))
const posts = (fake: ReturnType<typeof fakeFlowsApi>, tail: string) => fake.calls.filter((c) => c.method === 'POST' && c.path.endsWith(tail))

beforeEach(() => { vi.mocked(api.currentAgentRole).mockReturnValue('admin') })
afterEach(() => { vi.unstubAllGlobals() })

describe('편집기 왼쪽 — 버전 이력', () => {
  it('최신이 위, 배포 중 표시·note·만든 이를 보인다', async () => {
    fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1 })
    renderEditor()
    const p = await panel()
    const rows = await p.findAllByRole('listitem')
    expect(rows.map((r) => r.getAttribute('data-version'))).toEqual(['2', '1'])
    expect(rows[0].textContent).toContain('환불 문구 수정')
    expect(rows[0].textContent).toContain('박둘째')
    expect(within(rows[1]).getByText('배포 중')).toBeTruthy()
    expect(rows[0].textContent).not.toContain('배포 중')
  })

  it('버전 보기: 그 버전의 그래프를 읽기 전용으로 보이고, 초안으로 돌아올 수 있다', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V, graphs: { 1: OLD }, deployedVersion: 1 })
    renderEditor()
    expect(await within(await screen.findByTestId('flow-canvas')).findByText('새 답변')).toBeTruthy()
    await userEvent.click(await (await panel()).findByRole('button', { name: 'v1 보기' }))
    const canvas = within(screen.getByTestId('flow-canvas'))
    expect(await canvas.findByText('옛 답변')).toBeTruthy()
    expect(canvas.queryByText('새 답변')).toBeNull()
    expect(screen.getByText(/v1 보는 중/)).toBeTruthy()
    expect(fake.calls.some((c) => c.path.endsWith('/versions/1/'))).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: '초안으로 돌아가기' }))
    expect(await within(screen.getByTestId('flow-canvas')).findByText('새 답변')).toBeTruthy()
    expect(within(screen.getByTestId('flow-canvas')).queryByText('옛 답변')).toBeNull()
  })

  it('버전 보기 중에는 초안을 고치지 않는다(저장 호출 없음)', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V, graphs: { 1: OLD } })
    renderEditor()
    await userEvent.click(await (await panel()).findByRole('button', { name: 'v1 보기' }))
    await within(screen.getByTestId('flow-canvas')).findByText('옛 답변')
    expect(screen.queryByRole('button', { name: '삭제' })).toBeNull()
    expect(fake.patches()).toEqual([])
  })

  it('버전 저장: note 를 적어 보내면 새 버전이 맨 위에 생긴다', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V })
    renderEditor()
    const p = await panel()
    await userEvent.type(await p.findByLabelText('버전 메모'), '문구 다듬음')
    await userEvent.click(p.getByRole('button', { name: '버전 저장' }))
    await waitFor(() => expect(posts(fake, '/versions/')).toHaveLength(1))
    expect(posts(fake, '/versions/')[0].body).toEqual({ note: '문구 다듬음' })
    const rows = await p.findAllByRole('listitem')
    expect(rows[0].getAttribute('data-version')).toBe('3')
    expect(rows[0].textContent).toContain('문구 다듬음')
  })

  it('검증 오류로 버전 저장이 거절되면 이유를 보인다', async () => {
    fakeFlowsApi({ graph: DRAFT, versions: V, errors: [{ node_id: 'ai1', code: 'x', message: '지시문이 비었습니다' }] })
    renderEditor()
    const p = await panel()
    await userEvent.click(await p.findByRole('button', { name: '버전 저장' }))
    expect((await p.findByRole('alert')).textContent).toContain('초안에 오류')
  })
})

describe('배포·되돌리기', () => {
  it('시험 대화 없이 배포하면 한 번 확인하고, 확인하면 배포된다', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1 })
    renderEditor()
    const p = await panel()
    await userEvent.click(await p.findByRole('button', { name: 'v2 배포' }))
    expect(posts(fake, '/deploy/')).toHaveLength(0)
    expect(p.getByText(/시험 대화 없이/)).toBeTruthy()
    await userEvent.click(p.getByRole('button', { name: '그래도 배포' }))
    await waitFor(() => expect(posts(fake, '/deploy/')).toHaveLength(1))
    expect(posts(fake, '/deploy/')[0].body).toEqual({ version: 2 })
    await waitFor(() => expect(within(p.getAllByRole('listitem')[0]).getByText('배포 중')).toBeTruthy())
  })

  it('확인에서 취소하면 배포하지 않는다', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1 })
    renderEditor()
    const p = await panel()
    await userEvent.click(await p.findByRole('button', { name: 'v2 배포' }))
    await userEvent.click(p.getByRole('button', { name: '취소' }))
    expect(posts(fake, '/deploy/')).toHaveLength(0)
    expect(p.queryByText(/시험 대화 없이/)).toBeNull()
  })

  it('되돌리기는 한 번 눌러 바로 앞 버전으로 간다', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 2 })
    renderEditor()
    const p = await panel()
    await userEvent.click(await p.findByRole('button', { name: '되돌리기' }))
    await waitFor(() => expect(posts(fake, '/rollback/')).toHaveLength(1))
    await waitFor(() => expect(within(p.getAllByRole('listitem')[1]).getByText('배포 중')).toBeTruthy())
  })

  it('앞선 버전이 없으면 되돌리기를 끄고 이유를 보인다', async () => {
    fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1 })
    renderEditor()
    const b = await (await panel()).findByRole('button', { name: '되돌리기' })
    expect((b as HTMLButtonElement).disabled).toBe(true)
    expect(b.closest('[title]')?.getAttribute('title')).toContain('앞선 버전')
  })
})

describe('배포 승인 옵션이 켜진 조직', () => {
  it('배포 버튼이 「배포 요청」으로 바뀌고, 누르면 대기 요청이 보인다', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1, requiresApproval: true, me: { name: '나', email: 'me@x.kr' } })
    renderEditor()
    const p = await panel()
    await userEvent.click(await p.findByRole('button', { name: 'v2 배포 요청' }))
    await userEvent.click(p.getByRole('button', { name: '그래도 배포 요청' }))
    await waitFor(() => expect(posts(fake, '/deploy/')).toHaveLength(1))
    expect(await p.findByText(/v2 승인 대기/)).toBeTruthy()
    expect((p.getByRole('button', { name: 'v2 배포 요청' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('본인이 올린 요청은 승인 버튼이 꺼지고 이유가 보인다', async () => {
    fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1, requiresApproval: true, pending: REQ({ requested_by_name: '나' }), requests: [REQ({ requested_by_name: '나' })], me: { name: '나', email: 'me@x.kr' } })
    renderEditor()
    const p = await panel()
    const b = (await p.findByRole('button', { name: '승인' })) as HTMLButtonElement
    expect(b.disabled).toBe(true)
    expect(p.getByText(/다른 관리자가 승인/)).toBeTruthy()
  })

  it('다른 Admin 의 요청은 승인하면 배포되고 대기가 사라진다', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1, requiresApproval: true, pending: REQ(), requests: [REQ()], me: { name: '나', email: 'me@x.kr' } })
    renderEditor()
    const p = await panel()
    await userEvent.click(await p.findByRole('button', { name: '승인' }))
    await waitFor(() => expect(posts(fake, '/approve/')).toHaveLength(1))
    await waitFor(() => expect(p.queryByText(/승인 대기/)).toBeNull())
    expect(within(p.getAllByRole('listitem')[0]).getByText('배포 중')).toBeTruthy()
  })

  it('거절하면 사유 없이도 요청이 닫힌다', async () => {
    const fake = fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1, requiresApproval: true, pending: REQ(), requests: [REQ()] })
    renderEditor()
    const p = await panel()
    await userEvent.click(await p.findByRole('button', { name: '거절' }))
    await waitFor(() => expect(posts(fake, '/reject/')).toHaveLength(1))
    await waitFor(() => expect(p.queryByText(/승인 대기/)).toBeNull())
  })
})

describe('권한 없는 멤버', () => {
  it('저장·배포·되돌리기는 꺼지고 이유가 보인다, 보기는 된다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    fakeFlowsApi({ graph: DRAFT, versions: V, deployedVersion: 1, graphs: { 1: OLD } })
    renderEditor()
    const p = await panel()
    expect(p.queryByLabelText('버전 메모')).toBeNull()
    const d = (await p.findByRole('button', { name: 'v2 배포' })) as HTMLButtonElement
    expect(d.disabled).toBe(true)
    expect(d.closest('[title]')?.getAttribute('title')).toContain('관리자')
    expect((p.getByRole('button', { name: '되돌리기' }) as HTMLButtonElement).disabled).toBe(true)
    await userEvent.click(p.getByRole('button', { name: 'v1 보기' }))
    expect(await within(screen.getByTestId('flow-canvas')).findByText('옛 답변')).toBeTruthy()
  })
})
