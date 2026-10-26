import { describe, it, expect, vi, afterEach } from 'vitest'
import { lazyTryBackend } from './flowEditorBackend'
import { fakeFlowsApi } from '../flowsFakeApi'
import type { FlowGraph, PatchOp } from './flowGraph'

// 체험 편집기 백엔드의 지연 생성(#195): 세션 없는 동안의 위치 이동(Q2)·세션 생성 single-flight 를 fetch 경계에서 본다.
const GRAPH: FlowGraph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} },
    { id: 'ai1', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 0 }, config: {} },
  ],
  edges: [{ from: 'start', to: 'ai1' }],
}
const TEMPLATES = [{ id: 'sandcorp_home', name: 'Sandcorp 홈 상담', documents: [] }]
const MOVE: PatchOp = { op: 'upsert_node', id: 'ai1', position: { x: 500, y: 90 } }
const RENAME: PatchOp = { op: 'upsert_node', id: 'start', name: '처음' }
const posts = (fake: ReturnType<typeof fakeFlowsApi>) => fake.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/api/try/sessions/'))
const noop = () => {}
afterEach(() => { vi.unstubAllGlobals() })

describe('lazyTryBackend — 위치 이동만으로는 세션을 만들지 않는다(#194 Q2)', () => {
  it('위치만 바꾸는 연산은 서버에 보내지 않고 지역 그래프에 남긴다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    expect(await b.patchDraft('', [MOVE])).toEqual({ errors: [] })
    expect(posts(fake)).toHaveLength(0)
    expect(fake.patches()).toHaveLength(0)
    const again = await b.loadFlow('') // 저장 실패 뒤 되맞춤·StrictMode 재마운트도 이동을 잃지 않는다
    const moved = (again.draft.graph as unknown as FlowGraph).nodes.find((n) => n.id === 'ai1')!
    expect(moved.position).toEqual({ x: 500, y: 90 })
  })

  it('이동 뒤 첫 진짜 쓰기가 세션을 만들고 이동과 쓰기를 담은 지역 그래프를 POST 에 싣는다(연산 PATCH 없음)', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    await b.patchDraft('', [MOVE])
    await b.patchDraft('', [RENAME])
    expect(posts(fake)).toHaveLength(1)
    const sent = posts(fake)[0].body.graph as FlowGraph
    expect(sent.nodes.find((n) => n.id === 'ai1')!.position).toEqual({ x: 500, y: 90 })
    expect(sent.nodes.find((n) => n.id === 'start')!.name).toBe('처음')
    expect(fake.patches()).toHaveLength(0)
    await b.patchDraft('', [{ op: 'upsert_node', id: 'start', position: { x: 1, y: 2 } }]) // 세션이 생긴 뒤의 이동은 곧바로 저장
    expect(fake.patches()).toHaveLength(1)
    expect(posts(fake)).toHaveLength(1)
  })

  it('이동 뒤 시험 대화가 첫 쓰기면 이동이 담긴 그래프로 세션을 만든 뒤 턴을 돈다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, testTurns: [[{ event: 'done', data: { ran: true, handoff: false, note: '', errors: [], session_id: 's1', code: null } }]], try: { templates: TEMPLATES, session: null } })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    await b.patchDraft('', [MOVE])
    await b.streamTest('', '안녕', null, noop)
    const order = fake.calls.filter((c) => c.method !== 'GET').map((c) => `${c.method} ${c.path.replace(/^.*\/api\//, '/api/')}`)
    expect(order).toEqual(['POST /api/try/sessions/', 'POST /api/try/flows/f1/test/'])
    expect((posts(fake)[0].body.graph as FlowGraph).nodes.find((n) => n.id === 'ai1')!.position).toEqual({ x: 500, y: 90 })
  })
})

describe('lazyTryBackend — 세션 생성은 한 번(single-flight)', () => {
  it('저장 큐 밖(시험 대화·가입)과 큐 안의 첫 쓰기가 겹쳐도 POST 는 한 번이다', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null, startGate: () => gate } })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    const a = b.patchDraft('', [RENAME])
    const s = b.ensureSession()
    release()
    await Promise.all([a, s])
    expect(posts(fake)).toHaveLength(1)
    expect(await s).toBe('f1')
    expect(fake.patches()).toHaveLength(0) // 이름 변경은 POST 의 그래프에 이미 들어 있다
  })

  it('생성이 거절되면 지역 그래프는 그대로 남고, 다음 쓰기는 연산 재생 없이 지금까지의 그래프로 다시 만든다', async () => {
    let status = 429
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null, get startStatus() { return status } } })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    const first: PatchOp = { op: 'upsert_node', id: 'ai1', name: 'A' }
    await expect(b.patchDraft('', [first])).rejects.toThrow()
    const again = await b.loadFlow('') // 편집기는 실패 뒤 다시 읽는다 — 서버 초안이 아니라 지역 그래프가 와야 한다
    expect((again.draft.graph as unknown as FlowGraph).nodes.find((n) => n.id === 'ai1')!.name).toBe('A')
    status = 201
    await b.patchDraft('', [{ op: 'upsert_node', id: 'start', name: 'B' }])
    expect(posts(fake)).toHaveLength(2)
    const sent = posts(fake)[1].body.graph as FlowGraph
    expect(sent.nodes.map((n) => n.name)).toEqual(['B', 'A'])
    expect(fake.patches()).toHaveLength(0)
  })

  it('거절 중에는 세션 전 문구(첫 저장 때 체험이 시작돼요)·화면 유지, 생성 뒤에는 둘 다 바뀐다', async () => {
    let status = 429
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null, get startStatus() { return status } } })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    expect(b.savedLabel?.()).toBe('첫 저장 때 체험이 시작돼요')
    await expect(b.patchDraft('', [RENAME])).rejects.toThrow()
    expect(b.keepsEditsOnFailure?.()).toBe(true)
    status = 201
    await b.patchDraft('', [RENAME])
    expect(b.savedLabel?.()).toBeNull()
    expect(b.keepsEditsOnFailure?.()).toBe(false)
  })

  it('생성되면 알린다(머리줄 남은 턴·가입 링크)', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    const onSession = vi.fn()
    const b = lazyTryBackend('sandcorp_home', { onSession })
    await b.loadFlow('')
    await b.patchDraft('', [RENAME])
    expect(onSession).toHaveBeenCalledWith(expect.objectContaining({ flow_id: 'f1', turns_remaining: 20 }))
  })
})
