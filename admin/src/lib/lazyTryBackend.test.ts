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
const DRAFT_KEY = 'sandcorp-flow:try-draft:sandcorp_home'
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); sessionStorage.clear() })

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

const SESSION = { flow_id: 'f1', template: 'sandcorp_home', turns_remaining: 20 }
const nameOf = (g: FlowGraph, id: string) => g.nodes.find((n) => n.id === id)!.name

describe('lazyTryBackend — 세션 전 편집은 새로고침을 넘는다(#197, sessionStorage)', () => {
  it('위치 이동과 거절된 쓰기가 다시 마운트해도 남는다(세션·PATCH 없음)', async () => {
    let status = 429
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null, get startStatus() { return status } } })
    const a = lazyTryBackend('sandcorp_home')
    await a.loadFlow('')
    await a.patchDraft('', [MOVE])
    await expect(a.patchDraft('', [RENAME])).rejects.toThrow()
    const b = lazyTryBackend('sandcorp_home') // 새로고침 = 새 백엔드
    const g = (await b.loadFlow('')).draft.graph as unknown as FlowGraph
    expect(g.nodes.find((n) => n.id === 'ai1')!.position).toEqual({ x: 500, y: 90 })
    expect(nameOf(g, 'start')).toBe('처음')
    expect(fake.patches()).toHaveLength(0)
    status = 201 // 열린 뒤 다음 쓰기는 보관된 그래프로 세션을 만든다
    await b.patchDraft('', [{ op: 'upsert_node', id: 'ai1', name: 'X' }])
    const sent = posts(fake)[posts(fake).length - 1].body.graph as FlowGraph
    expect(sent.nodes.find((n) => n.id === 'ai1')!.position).toEqual({ x: 500, y: 90 })
    expect(nameOf(sent, 'start')).toBe('처음')
  })

  it('세션이 생기면 보관본을 지운다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    await b.patchDraft('', [MOVE])
    expect(sessionStorage.getItem(DRAFT_KEY)).not.toBeNull()
    await b.patchDraft('', [RENAME])
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull()
  })

  it('템플릿마다 따로 보관한다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: [...TEMPLATES, { id: 'other', name: '다른', documents: [] }], session: null } })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    await b.patchDraft('', [MOVE])
    const o = lazyTryBackend('other')
    const g = (await o.loadFlow('')).draft.graph as unknown as FlowGraph
    expect(g.nodes.find((n) => n.id === 'ai1')!.position).toEqual({ x: 260, y: 0 })
  })

  it('저장소 접근이 던져도 메모리만으로 편집이 계속된다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    const b = lazyTryBackend('sandcorp_home')
    await b.loadFlow('')
    await b.patchDraft('', [MOVE])
    const g = (await b.loadFlow('')).draft.graph as unknown as FlowGraph
    expect(g.nodes.find((n) => n.id === 'ai1')!.position).toEqual({ x: 500, y: 90 })
  })

  it('보관본이 깨졌으면 템플릿으로 연다', async () => {
    fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: null } })
    sessionStorage.setItem(DRAFT_KEY, '{not json')
    const g = (await lazyTryBackend('sandcorp_home').loadFlow('')).draft.graph as unknown as FlowGraph
    expect(g.nodes).toHaveLength(2)
  })
})

describe('lazyTryBackend — 생성 직전 확인(Q6)과 안내(#197)', () => {
  it('생성 직전에 같은 템플릿 세션이 이미 있으면(다른 탭) POST 없이 그 세션을 쓰고 지역 그래프로 덮는다', async () => {
    let session: typeof SESSION | null = null
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: () => session } })
    const onSession = vi.fn()
    const b = lazyTryBackend('sandcorp_home', { onSession })
    await b.loadFlow('')
    session = SESSION // 다른 탭이 그 사이에 세션을 만들었다
    await b.patchDraft('', [RENAME])
    expect(posts(fake)).toHaveLength(0)
    expect(fake.puts()).toHaveLength(1)
    expect(nameOf(fake.puts()[0] as unknown as FlowGraph, 'start')).toBe('처음') // 지역 편집이 이긴다
    expect(onSession).toHaveBeenCalledWith(expect.objectContaining({ flow_id: 'f1' }))
    expect(sessionStorage.getItem(DRAFT_KEY)).toBeNull()
    await b.patchDraft('', [{ op: 'upsert_node', id: 'ai1', name: 'Y' }]) // 이후는 보통의 PATCH
    expect(fake.patches()).toHaveLength(1)
  })

  it('다른 템플릿의 세션이 진행 중이면 열 때 알리고, 첫 쓰기 전까지 POST 는 없다', async () => {
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: { ...SESSION, template: 'other' } } })
    const onOtherSession = vi.fn()
    const b = lazyTryBackend('sandcorp_home', { onOtherSession })
    await b.loadFlow('')
    await b.patchDraft('', [MOVE])
    expect(onOtherSession).toHaveBeenCalledWith('other')
    expect(posts(fake)).toHaveLength(0)
    await b.patchDraft('', [RENAME]) // 첫 쓰기: 새 체험으로 바뀐다(생성 직전 확인은 다른 템플릿이라 POST)
    expect(posts(fake)).toHaveLength(1)
  })
})

describe('lazyTryBackend — 편집 중 만료(Q5)', () => {
  it('세션이 사라지면 안내하고 지역 모드로 돌아가 편집을 지키며, 다음 쓰기가 새 세션을 만든다', async () => {
    let session: typeof SESSION | null = SESSION
    const fake = fakeFlowsApi({ graph: GRAPH, try: { templates: TEMPLATES, session: () => session } })
    const onExpired = vi.fn()
    const b = lazyTryBackend('sandcorp_home', { onExpired })
    await b.loadFlow('')
    expect(b.keepsEditsOnFailure?.()).toBe(false)
    await b.patchDraft('', [RENAME]) // 서버 세션에 저장
    session = null // 24시간이 지났다
    await expect(b.patchDraft('', [{ op: 'upsert_node', id: 'ai1', name: 'Z' }])).rejects.toThrow()
    expect(onExpired).toHaveBeenCalledTimes(1)
    expect(b.keepsEditsOnFailure?.()).toBe(true)
    expect(b.savedLabel?.()).toBe('첫 저장 때 체험이 시작돼요')
    const g = (await b.loadFlow('')).draft.graph as unknown as FlowGraph // 편집기가 다시 읽어도 지금까지의 편집
    expect(nameOf(g, 'ai1')).toBe('Z')
    expect(nameOf(g, 'start')).toBe('처음')
    await b.patchDraft('', [{ op: 'upsert_node', id: 'ai1', name: 'W' }])
    expect(posts(fake)).toHaveLength(1)
    const sent = posts(fake)[0].body.graph as FlowGraph
    expect(nameOf(sent, 'ai1')).toBe('W')
    expect(nameOf(sent, 'start')).toBe('처음')
  })
})
