// 시험용: 흐름 API(/api/tenant/flows/...)를 흉내 내는 fetch 대역. 네트워크 경계만 바꾸고 화면·생성 클라이언트는 실제를 쓴다.
import { vi } from 'vitest'
import type { FlowGraph, FlowError } from './lib/flowGraph'
import nodeSchema from './lib/__fixtures__/nodeSchema.json'

export type FakeVersion = { number: number; note: string; created_by_name: string | null; created_at: string; deployed?: boolean; hash?: string }
export type FakeRequest = { id: string; flow_id: string; flow_name: string; version: number; status: string; requested_by_name: string | null; decided_by_name: string | null; decided_at: string | null; decision_note: string; created_at: string }

export type Call = { method: string; path: string; body: any; init?: RequestInit }

export function fakeFlowsApi(opts: {
  graph: FlowGraph
  errors?: FlowError[]
  flows?: object[]
  patchStatus?: number
  patchBody?: object
  /** 시험 대화 POST 가 차례로 돌려줄 SSE 이벤트들(턴마다 하나). 다 쓰면 마지막 것을 되풀이한다. */
  testTurns?: { event: string; data: object }[][]
  testStatus?: number
  /** DELETE /flows/{id}/ (보관)이 돌려줄 상태(기본 204). 409면 {detail, code:'flow_in_use'}. */
  archiveStatus?: number
  /** POST /flows/{id}/embed/ 가 돌려줄 상태(기본 200). 409면 {detail, code:'not_embeddable'}. 200이면 본문의 enabled로 바뀐 흐름을 돌려준다. */
  embedStatus?: number
  /** true면 버전 저장이 「마지막 버전과 같음」(created:false)으로 새 버전을 만들지 않는다. */
  duplicateVersion?: boolean
  trial?: { state: string; remaining: number; limit: number } | (() => { state: string; remaining: number; limit: number })  // 함수면 호출할 때마다 계산(턴 뒤 감소 흉내)
  /** 버전 이력(번호 오름차순) — 없으면 비어 있다. graphs 에 번호별 그래프를 주면 「버전 보기」가 그것을 돌려준다. */
  versions?: FakeVersion[]
  graphs?: Record<number, FlowGraph>
  deployedVersion?: number | null
  requiresApproval?: boolean
  pending?: FakeRequest | null
  /** GET /deploy-requests/ 가 돌려줄 목록(상태 필터를 따른다). */
  requests?: FakeRequest[]
  me?: { name: string; email: string }
  /** GET /sessions/{id}/decision-traces/ 가 돌려줄 값(세션 id → 응답). 없는 세션은 404. */
  traces?: Record<string, object>
  /** 공개 흐름 보기 API(GET /api/public/flows/{slug}/, #39) — slug → 응답 머리(그래프는 opts.graph). 없는 slug 는 404. */
  public?: Record<string, { brand_name: string; flow_name: string; version: number; template: string | null }>
  /** 공개 체험 API(/api/try/…) 대역 — 주면 템플릿·세션 응답도 흉내 낸다(#37). */
  try?: {
    templates?: { id: string; name: string; documents: string[] }[]
    /** GET session/ 이 돌려줄 값. null 이면 200 null(#163). */
    session?: { flow_id: string; template: string; turns_remaining: number } | null | (() => { flow_id: string; template: string; turns_remaining: number } | null)
    /** POST sessions/ 의 상태(기본 201, 본문은 session). 429 면 {detail, code:'try_ip_limit'}. */
    startStatus?: number
    /** 주면 POST sessions/ 응답을 이 약속이 풀릴 때까지 붙잡는다(생성 중에 편집이 더 들어오는 경우, #195). */
    startGate?: () => Promise<void>
  }
}) {
  const calls: Call[] = []
  const state = { errors: opts.errors ?? [] }
  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const versions: FakeVersion[] = [...(opts.versions ?? [])]
  const st = { deployed: opts.deployedVersion ?? null as number | null, pending: opts.pending ?? null as FakeRequest | null, requests: [...(opts.requests ?? [])] }
  let listed: object[] | null = null
  const flowList = () => (listed ??= [...(opts.flows ?? [flowOut()])])
  const flowOut = () => ({ id: 'f1', name: '기본 흐름', is_public: false, embed_key: 'abcd1234', embed_enabled: false, embed_path: '/chatbot/acme/abcd1234/', deployed_version: st.deployed, updated_at: '2026-10-01T09:00:00Z', has_pending_request: st.pending !== null })
  const versionOut = (v: FakeVersion) => ({ hash: `h${v.number}`, ...v, deployed: v.number === st.deployed })
  const newRequest = (version: number): FakeRequest => ({ id: `r${st.requests.length + 1}`, flow_id: 'f1', flow_name: '기본 흐름', version, status: 'pending', requested_by_name: opts.me?.name ?? '나', decided_by_name: null, decided_at: null, decision_note: '', created_at: '2026-10-02T09:00:00Z' })
  let turnNo = 0
  let createdGraph: FlowGraph | null = null // POST sessions/ 가 실어 받은 그래프
  let created: { flow_id: string; template: string; turns_remaining: number } | null = null // POST sessions/ 가 만든 세션(쿠키 흉내)
  const sse = (events: { event: string; data: object }[]) => {
    const text = events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join('')
    // 작은 조각으로 쪼개 보내 스트림 읽기가 조각 경계를 견디는지도 함께 본다.
    const chunks = text.match(/[\s\S]{1,37}/g) ?? []
    const enc = new TextEncoder()
    return new Response(new ReadableStream({ start(c) { chunks.forEach((x) => c.enqueue(enc.encode(x))); c.close() } }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, path, body, init })
    const pub = path.match(/\/api\/public\/flows\/([^/]+)\/$/)
    if (pub) {
      const head = opts.public?.[decodeURIComponent(pub[1])]
      return head && method === 'GET' ? json(200, { slug: pub[1], ...head, graph: opts.graph }) : json(404, { detail: 'Not Found' })
    }
    if (path.includes('/api/try/')) {
      const t = opts.try ?? {}
      const sess = typeof t.session === 'function' ? t.session() : (t.session ?? created)
      if (method === 'GET' && path.endsWith('/api/try/templates/')) return json(200, t.templates ?? [])
      const td = path.match(/\/api\/try\/templates\/([^/]+)\/$/)
      if (method === 'GET' && td) {
        const found = (t.templates ?? []).find((x) => x.id === td[1])
        return found ? json(200, { ...found, graph: opts.graph }) : json(404, { detail: '알 수 없는 예제입니다.', code: 'unknown_template' })
      }
      if (method === 'GET' && path.endsWith('/api/try/session/')) return json(200, sess ? { expires_at: '2026-10-26T09:00:00Z', ...sess } : null)
      if (method === 'POST' && path.endsWith('/api/try/sessions/')) {
        if (t.startGate) await t.startGate()
        if (t.startStatus && t.startStatus !== 201) return json(t.startStatus, { detail: '오늘 이 네트워크에서 만들 수 있는 체험을 모두 만들었어요. 내일 다시 찾아 주세요.', code: 'try_ip_limit' })
        created = sess ?? { flow_id: 'f1', template: body.template, turns_remaining: 20 }
        if (body.graph) createdGraph = body.graph // 그래프를 실어 만들면 사본 초안이 그것이다(#196)
        return json(201, { expires_at: '2026-10-26T09:00:00Z', ...created, draft: body.graph ? { graph: body.graph, errors: state.errors, valid: state.errors.length === 0 } : null })
      }
      if (method === 'GET' && path.endsWith('/api/try/node-schema/')) return json(200, { kinds: nodeSchema })
      if (!sess && !path.endsWith('/api/try/node-schema/')) return json(401, { detail: '체험 세션이 없습니다.', code: 'try_no_session' })
      if (method === 'POST' && path.endsWith('/deploy/')) return json(403, { detail: '체험 흐름은 배포할 수 없습니다.', code: 'try_restricted' })
    }
    if (method === 'GET' && path.endsWith('/api/tenant/flows/')) return json(200, flowList())
    const archived = method === 'DELETE' ? path.match(/\/api\/tenant\/flows\/([^/]+)\/$/) : null
    if (archived) {
      if (opts.archiveStatus && opts.archiveStatus !== 204) return json(opts.archiveStatus, { detail: '위젯이 쓰고 있는 흐름이라 보관할 수 없습니다. 먼저 다른 흐름을 배포하세요.', code: 'flow_in_use' })
      listed = flowList().filter((f) => (f as { id: string }).id !== archived[1])
      return new Response(null, { status: 204 })
    }
    const embedCall = method === 'POST' ? path.match(/\/api\/tenant\/flows\/([^/]+)\/(embed|default)\/$/) : null
    if (embedCall) {
      if (opts.embedStatus && opts.embedStatus !== 200) return json(opts.embedStatus, { detail: '아직 배포한 버전이 없어 임베드를 켤 수 없습니다. 먼저 배포하세요.', code: 'not_embeddable' })
      const base = (flowList().find((f) => (f as { id: string }).id === embedCall[1]) ?? flowOut()) as Record<string, unknown>
      const next = embedCall[2] === 'embed' ? { ...base, embed_enabled: !!body.enabled } : { ...base, is_public: true }
      listed = flowList().map((f) => ((f as { id: string }).id === embedCall[1] ? next : embedCall[2] === 'default' ? { ...(f as object), is_public: false } : f))
      return json(200, next)
    }
    if (method === 'POST' && path.endsWith('/api/tenant/flows/')) return json(201, { ...flowOut(), id: 'f2', name: body.name })
    if (method === 'POST' && path.endsWith('/test/')) {
      if (opts.testStatus && opts.testStatus !== 200) return json(opts.testStatus, { detail: '권한이 없습니다.' })
      const turns = opts.testTurns ?? []
      return sse(turns[Math.min(turnNo++, turns.length - 1)] ?? [])
    }
    if (method === 'GET' && path.endsWith('/api/tenant/trial/')) return json(200, (typeof opts.trial === 'function' ? opts.trial() : opts.trial) ?? { state: 'byok', remaining: 0, limit: 20 })
    if (method === 'GET' && path.endsWith('/api/tenant/flows/node-schema/')) return json(200, { kinds: nodeSchema })
    if (method === 'GET' && path.endsWith('/api/tenant/members/me')) return json(200, { id: 'm1', tenant_id: 't1', tenant_name: '조직', role: 'admin', ...(opts.me ?? { name: '나', email: 'me@x.kr' }) })
    if (method === 'GET' && path.endsWith('/api/tenant/config/')) return json(200, { deploy_requires_approval: !!opts.requiresApproval })
    let m = path.match(/\/flows\/[^/]+\/versions\/(\d+)\/$/)
    if (m && method === 'GET') {
      const v = versions.find((x) => x.number === Number(m![1]))
      return v ? json(200, { ...versionOut(v), graph: opts.graphs?.[v.number] ?? opts.graph }) : json(404, { detail: '없는 버전', code: 'version_not_found' })
    }
    if (method === 'POST' && path.endsWith('/versions/')) {
      if (state.errors.length) return json(422, { detail: '초안에 오류가 있어 저장할 수 없습니다.', code: 'invalid_flow', errors: state.errors })
      if (opts.duplicateVersion) return json(200, { created: false, version: versionOut(versions[versions.length - 1]) })
      const v: FakeVersion = { number: versions.length + 1, note: body?.note ?? '', created_by_name: opts.me?.name ?? '나', created_at: '2026-10-02T10:00:00Z' }
      versions.push(v)
      return json(201, { created: true, version: versionOut(v) })
    }
    if (method === 'POST' && path.endsWith('/deploy/')) {
      if (opts.requiresApproval) {
        const r = newRequest(body.version)
        if (st.pending) st.pending.status = 'superseded'
        st.pending = r
        st.requests.push(r)
        return json(202, { deployed: false, flow: flowOut(), request: r })
      }
      st.deployed = body.version
      return json(200, { deployed: true, flow: flowOut(), request: null })
    }
    if (method === 'POST' && path.endsWith('/rollback/')) {
      const prev = versions.map((v) => v.number).filter((n) => n < (st.deployed ?? 0)).pop()
      if (prev === undefined) return json(404, { detail: '앞선 버전이 없습니다.', code: 'version_not_found' })
      st.deployed = prev
      return json(200, flowOut())
    }
    if (method === 'GET' && path.includes('/api/tenant/deploy-requests/')) {
      const status = new URL(path, 'http://x').searchParams.get('status')
      return json(200, st.requests.filter((r) => !status || r.status === status))
    }
    m = path.match(/\/deploy-requests\/([^/]+)\/(approve|reject)\/$/)
    if (m && method === 'POST') {
      const r = st.requests.find((x) => x.id === m![1])
      if (!r) return json(404, { detail: '없는 요청' })
      if (r.status !== 'pending') return json(409, { detail: '이미 처리된 요청입니다.', code: 'request_not_pending' })
      if (m[2] === 'approve' && r.requested_by_name === (opts.me?.name ?? '나')) return json(403, { detail: '자기가 올린 배포 요청은 승인할 수 없습니다.', code: 'self_approval' })
      r.status = m[2] === 'approve' ? 'approved' : 'rejected'
      r.decided_by_name = '다른 관리자'
      if (m[2] === 'approve') st.deployed = r.version
      if (st.pending?.id === r.id) st.pending = null
      return json(200, r)
    }
    if (method === 'GET' && /\/flows\/[^/]+\/$/.test(path)) {
      return json(200, { flow: flowOut(), draft: { graph: (path.includes('/api/try/') && createdGraph) || opts.graph, errors: state.errors, valid: state.errors.length === 0 }, versions: versions.map(versionOut), pending_request: st.pending })
    }
    if (method === 'PUT' && path.endsWith('/draft/')) {
      createdGraph = body.graph // 통째 교체(#197: 이미 있던 세션의 사본을 지역 그래프로 덮는다)
      return json(200, { graph: body.graph, errors: state.errors, valid: state.errors.length === 0 })
    }
    if (method === 'PATCH' && path.endsWith('/draft/')) {
      if (opts.patchStatus && opts.patchStatus !== 200) return json(opts.patchStatus, opts.patchBody ?? { detail: '실패', code: 'invalid_patch', index: 0 })
      return json(200, { graph: opts.graph, errors: state.errors, valid: state.errors.length === 0 })
    }
    const tr = path.match(/\/api\/tenant\/sessions\/([^/]+)\/decision-traces/)
    if (method === 'GET' && tr) return opts.traces?.[tr[1]] ? json(200, opts.traces[tr[1]]) : json(404, { detail: '없는 세션' })
    return json(404, { detail: 'no' })
  })
  vi.stubGlobal('fetch', fn)
  return { calls, fetch: fn, /** 지금까지 받은 응답의 {경로, 상태}. */ statuses: async () => Promise.all(fn.mock.results.map(async (r, i) => ({ path: calls[i].path, status: (await r.value as Response).status }))), puts: () => calls.filter((c) => c.method === 'PUT' && c.path.endsWith('/draft/')).map((c) => c.body.graph), patches: () => calls.filter((c) => c.method === 'PATCH' && c.path.endsWith('/draft/')).map((c) => c.body.ops) }
}
