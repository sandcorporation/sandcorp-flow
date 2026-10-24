import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import * as api from '../api'
import FlowEditorPage from './FlowEditorPage'
import { fakeFlowsApi } from '../flowsFakeApi'
import type { FlowGraph, FlowNodeJson, FlowError } from '../lib/flowGraph'

vi.mock('../api', () => ({ currentAgentRole: vi.fn(), listAllowedDomains: vi.fn(), listSecrets: vi.fn() }))

const node = (id: string, kind: string, name: string, config: Record<string, unknown>, n: number): FlowNodeJson =>
  ({ id, kind, code: `N-0${n}`, name, position: { x: n * 200, y: 0 }, config })

const GRAPH: FlowGraph = {
  nodes: [
    node('start', 'start', '시작', {}, 1),
    node('ai1', 'ai_answer', '답변', { instructions: '친절하게' }, 2),
    node('cond1', 'condition', '분기', { branches: [{ name: 'refund', when: "label == 'refund'" }] }, 3),
    node('cls1', 'ai_classify', '분류', { labels: ['refund', 'other'] }, 4),
    node('ks1', 'knowledge_search', '검색', { top_k: 5 }, 5),
    node('human1', 'handoff', '상담원', { reason: '' }, 6),
    node('guard1', 'guardrail', '검사', { rules: ['pii'], action: 'block', target: 'output' }, 7),
    node('http1', 'http_tool', '주문 조회', { method: 'GET', url: 'https://api.example.com/o', headers: {}, body_template: null }, 8),
  ],
  edges: [],
}

async function pick(text: string) {
  const card = (await screen.findAllByText(text)).map((el) => el.closest('[data-node-id]')).find(Boolean)!
  fireEvent.click(card)
}
function renderEditor() {
  return render(
    <MemoryRouter initialEntries={['/tenant/flows/f1']}>
      <Routes><Route path="/tenant/flows/:id" element={<FlowEditorPage />} /></Routes>
    </MemoryRouter>,
  )
}
const lastConfig = (fake: ReturnType<typeof fakeFlowsApi>, id: string) => {
  const ops = fake.patches().flat().filter((o: any) => o.op === 'upsert_node' && o.id === id && o.config)
  return ops[ops.length - 1]?.config
}

beforeEach(() => {
  vi.mocked(api.currentAgentRole).mockReturnValue('admin')
  vi.mocked(api.listAllowedDomains).mockResolvedValue([])
  vi.mocked(api.listSecrets).mockResolvedValue([])
})
afterEach(() => { vi.unstubAllGlobals() })

describe('노드 설정 패널 — 종류별 폼', () => {
  it('가드레일: 경고띠와 검사 종류·처리·대상 폼. 처리를 바꾸면 config 통째로 저장한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('검사')
    expect(await screen.findByText(/나가는 말을 바꾸거나 막을 수 있습니다/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: '개인정보(전화·주민번호·카드·이메일)' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '금지어' })).not.toBeChecked()
    await userEvent.selectOptions(screen.getByLabelText('처리'), 'mask')
    await waitFor(() => expect(lastConfig(fake, 'guard1')).toEqual({ rules: ['pii'], action: 'mask', target: 'output' }))
    await userEvent.click(screen.getByRole('checkbox', { name: '금지어' }))
    await waitFor(() => expect(lastConfig(fake, 'guard1')).toEqual({ rules: ['pii', 'banned_words'], action: 'mask', target: 'output' }))
  })

  it('가드레일 금지어는 한 줄에 하나, 막기 문구는 비우면 기본 문구로 돌린다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('검사')
    const words = await screen.findByLabelText('금지어 목록')
    fireEvent.change(words, { target: { value: '환불\n공짜' } })
    fireEvent.blur(words)
    await waitFor(() => expect(lastConfig(fake, 'guard1')).toMatchObject({ banned_words: ['환불', '공짜'] }))
  })

  it('조건: 갈래를 더하고 식을 쓰면 branches 로 저장하고, 쓸 수 있는 상태 키를 안내한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('분기')
    expect(await screen.findByText(/label/)).toBeInTheDocument()
    expect(screen.getByText(/confidence/)).toBeInTheDocument()
    expect(screen.getByText(/user_message/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '갈래 추가' }))
    const names = screen.getAllByLabelText('갈래 이름')
    fireEvent.change(names[1], { target: { value: 'low' } })
    fireEvent.blur(names[1])
    const whens = screen.getAllByLabelText('조건식')
    fireEvent.change(whens[1], { target: { value: 'confidence < 0.5' } })
    fireEvent.blur(whens[1])
    await waitFor(() => expect(lastConfig(fake, 'cond1')).toEqual({
      branches: [{ name: 'refund', when: "label == 'refund'" }, { name: 'low', when: 'confidence < 0.5' }],
    }))
  })

  it('AI 분류·추출: 라벨은 한 줄에 하나, 추출 필드는 이름·설명·형식 줄로 더한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('분류')
    const labels = await screen.findByLabelText('분류 라벨')
    expect(labels).toHaveValue('refund\nother')
    fireEvent.change(labels, { target: { value: 'refund\nother\nshipping' } })
    fireEvent.blur(labels)
    await waitFor(() => expect(lastConfig(fake, 'cls1')).toEqual({ labels: ['refund', 'other', 'shipping'] }))
    await userEvent.click(screen.getByRole('button', { name: '추출 필드 추가' }))
    await userEvent.selectOptions(screen.getByLabelText('형식'), 'number')
    await waitFor(() => expect(lastConfig(fake, 'cls1').fields).toEqual([{ name: '', type: 'number' }]))
  })

  it('지식 검색 top_k, AI 답변 되묻기·instructions, 사람 넘김 사유를 폼으로 고친다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('검색')
    const k = await screen.findByLabelText('가져올 근거 수')
    fireEvent.change(k, { target: { value: '8' } })
    fireEvent.blur(k)
    await waitFor(() => expect(lastConfig(fake, 'ks1')).toEqual({ top_k: 8 }))
    await pick('답변')
    await userEvent.click(await screen.findByRole('checkbox', { name: /답한 뒤 방문자의 답을 기다립니다/ }))
    await waitFor(() => expect(lastConfig(fake, 'ai1')).toEqual({ instructions: '친절하게', await_reply: true }))
    await pick('상담원')
    const reason = await screen.findByLabelText('넘기는 사유')
    fireEvent.change(reason, { target: { value: '환불 요청' } })
    fireEvent.blur(reason)
    await waitFor(() => expect(lastConfig(fake, 'human1')).toEqual({ reason: '환불 요청' }))
  })

  it('시작 노드는 설정할 것이 없다고 말한다', async () => {
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('시작')
    expect(await screen.findByText(/설정할 항목이 없습니다/)).toBeInTheDocument()
  })

  it('HTTP 도구: 메서드·주소·헤더 표. 인증 헤더에 비밀 원문을 쓰면 {{secret.이름}} 참조를 안내한다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('주문 조회')
    await userEvent.selectOptions(await screen.findByLabelText('메서드'), 'POST')
    await waitFor(() => expect(lastConfig(fake, 'http1')).toMatchObject({ method: 'POST' }))
    await userEvent.click(screen.getByRole('button', { name: '헤더 추가' }))
    const hn = screen.getByLabelText('헤더 이름')
    fireEvent.change(hn, { target: { value: 'Authorization' } })
    const hv = screen.getByLabelText('헤더 값')
    fireEvent.change(hv, { target: { value: 'sk-live-123' } })
    fireEvent.blur(hv)
    expect(await screen.findByText(/비밀 원문 대신 \{\{secret\.이름\}\} 참조/)).toBeInTheDocument()
    expect(await screen.findByText(/등록된 비밀이 없습니다/)).toBeInTheDocument()
    fireEvent.change(hv, { target: { value: 'Bearer {{secret.shop_key}}' } })
    fireEvent.blur(hv)
    await waitFor(() => expect(lastConfig(fake, 'http1').headers).toEqual({ Authorization: 'Bearer {{secret.shop_key}}' }))
    expect(screen.queryByText(/비밀 원문 대신/)).toBeNull()
  })

  it('HTTP 도구: 응답→상태 표(save)에 행을 더하고 지운다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('주문 조회')
    await userEvent.click(await screen.findByRole('button', { name: '저장할 값 추가' }))
    fireEvent.change(screen.getByLabelText('상태 이름'), { target: { value: 'order_status' } })
    const path = screen.getByLabelText('응답 경로')
    fireEvent.change(path, { target: { value: '$.data.status' } })
    fireEvent.blur(path)
    await waitFor(() => expect(lastConfig(fake, 'http1').save).toEqual({ order_status: '$.data.status' }))
    await userEvent.click(screen.getByRole('button', { name: '저장할 값 1번 삭제' }))
    await waitFor(() => expect(lastConfig(fake, 'http1').save).toBeUndefined())
  })

  it('HTTP 도구: 시험 대화가 실제 API를 호출한다는 안내와 error 갈래 설명을 보인다', async () => {
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('주문 조회')
    expect(await screen.findByText(/시험 대화도 이 API를 실제로 호출합니다/)).toBeInTheDocument()
    expect(screen.getByText(/POST면 실제로 접수/)).toBeInTheDocument()
    expect(screen.getByText(/error 갈래/)).toBeInTheDocument()
  })

  it('HTTP 도구: 주소 호스트가 허용 목록 밖이면 경고와 설정 탭 링크를 보이고, 안이면 없다', async () => {
    vi.mocked(api.listAllowedDomains).mockResolvedValue([{ id: 'd1', pattern: '*.example.com', created_at: '' }])
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('주문 조회')
    // https://api.example.com/o 는 *.example.com 한 단계 하위 → 허용
    await screen.findByLabelText('주소(https)')
    await waitFor(() => expect(api.listAllowedDomains).toHaveBeenCalled())
    expect(screen.queryByText(/허용 도메인에 없습니다/)).toBeNull()
    const url = screen.getByLabelText('주소(https)')
    fireEvent.change(url, { target: { value: 'https://other.io/x' } })
    fireEvent.blur(url)
    await waitFor(() => expect(lastConfig(fake, 'http1').url).toBe('https://other.io/x'))
    expect(await screen.findByText(/other\.io.*허용 도메인에 없습니다/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /설정에서 허용 도메인 추가/ })).toHaveAttribute('href', '/tenant/config?section=secrets')
  })

  it('HTTP 도구: 등록되지 않은 {{secret.이름}} 을 경고하고 등록된 이름은 목록으로 보인다', async () => {
    vi.mocked(api.listSecrets).mockResolvedValue([{ name: 'shop_key', created_at: '', updated_at: '' }])
    const g: FlowGraph = { ...GRAPH, nodes: GRAPH.nodes.map((n) => n.id === 'http1' ? { ...n, config: { ...n.config, headers: { Authorization: 'Bearer {{secret.shop_key}}', 'X-A': '{{secret.nope}}' } } } : n) }
    fakeFlowsApi({ graph: g })
    renderEditor()
    await pick('주문 조회')
    expect(await screen.findByText(/등록되지 않은 비밀: nope/)).toBeInTheDocument()
    expect(screen.queryByText(/등록되지 않은 비밀:.*shop_key/)).toBeNull()
    expect(screen.getByText(/등록된 비밀:.*shop_key/)).toBeInTheDocument()
  })

  it('HTTP 도구: 서버 검증 오류(url·save 경로)를 해당 필드 아래에, unknown_secret 은 노드 오류로 보인다', async () => {
    const errors: FlowError[] = [
      { node_id: 'http1', code: 'invalid_config', message: 'url: Value error, url의 호스트는 고정해야 합니다; save: Value error, save \'x\': 경로 형식 오류' },
      { node_id: 'http1', code: 'unknown_secret', message: '등록되지 않은 비밀: nope' },
    ]
    fakeFlowsApi({ graph: GRAPH, errors })
    renderEditor()
    await pick('주문 조회')
    expect(await screen.findByText(/호스트는 고정해야 합니다/)).toBeInTheDocument()
    expect(screen.getByText(/경로 형식 오류/)).toBeInTheDocument()
    expect(screen.getAllByText(/unknown_secret/).length).toBeGreaterThan(0)
  })

  it('HTTP 도구: 역할이 admin 이 아니면 읽기 전용이다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('주문 조회')
    expect(await screen.findByLabelText('주소(https)')).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: '저장할 값 추가' })).toBeNull()
  })

  it('검증 오류의 경로를 해당 필드 아래에 보인다', async () => {
    const errors: FlowError[] = [
      { node_id: 'guard1', code: 'invalid_config', message: 'rules: List should have at least 1 item after validation, not 0; 가격 약속은 부분을 가릴 수 없습니다' },
      { node_id: 'cond1', code: 'invalid_config', message: 'branches.0.when: when 식 오류: 모르는 이름' },
    ]
    fakeFlowsApi({ graph: GRAPH, errors })
    renderEditor()
    await pick('검사')
    const rules = await screen.findByRole('group', { name: '검사 종류' })
    expect(within(rules).getByText(/at least 1 item/)).toBeInTheDocument()
    expect(screen.getByRole('alert', { name: '' })).toHaveTextContent('가격 약속은 부분을 가릴 수 없습니다')
    await pick('분기')
    const when = (await screen.findAllByLabelText('조건식'))[0]
    expect(when.closest('div')!.parentElement).toHaveTextContent('when 식 오류: 모르는 이름')
    expect(when).toHaveAttribute('aria-invalid', 'true')
  })

  it('「고급」 탭에서 JSON 으로 고치고 폼으로 돌아오면 반영돼 있다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('분류')
    await userEvent.click(await screen.findByRole('tab', { name: '고급' }))
    const box = screen.getByLabelText('설정(JSON)')
    fireEvent.change(box, { target: { value: '{"labels":["x"]}' } })
    await userEvent.click(screen.getByRole('button', { name: '설정 적용' }))
    await waitFor(() => expect(lastConfig(fake, 'cls1')).toEqual({ labels: ['x'] }))
    await userEvent.click(screen.getByRole('tab', { name: '폼' }))
    expect(screen.getByLabelText('분류 라벨')).toHaveValue('x')
  })

  it('권한 없는 멤버는 폼이 읽기 전용이고 추가·삭제 버튼이 없다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    const fake = fakeFlowsApi({ graph: GRAPH })
    renderEditor()
    await pick('분기')
    expect(await screen.findAllByLabelText('조건식')).toSatisfy((els: HTMLElement[]) => els.every((e) => e.hasAttribute('readonly')))
    expect(screen.queryByRole('button', { name: '갈래 추가' })).toBeNull()
    await pick('검사')
    expect(await screen.findByRole('checkbox', { name: '금지어' })).toBeDisabled()
    expect(screen.getByLabelText('처리')).toBeDisabled()
    expect(fake.patches()).toEqual([])
  })
})
