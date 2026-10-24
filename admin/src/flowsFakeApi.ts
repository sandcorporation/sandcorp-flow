// 시험용: 흐름 API(/api/tenant/flows/...)를 흉내 내는 fetch 대역. 네트워크 경계만 바꾸고 화면·생성 클라이언트는 실제를 쓴다.
import { vi } from 'vitest'
import type { FlowGraph, FlowError } from './lib/flowGraph'
import nodeSchema from './lib/__fixtures__/nodeSchema.json'

export type Call = { method: string; path: string; body: any }

export function fakeFlowsApi(opts: {
  graph: FlowGraph
  errors?: FlowError[]
  flows?: object[]
  patchStatus?: number
  patchBody?: object
  /** 시험 대화 POST 가 차례로 돌려줄 SSE 이벤트들(턴마다 하나). 다 쓰면 마지막 것을 되풀이한다. */
  testTurns?: { event: string; data: object }[][]
  testStatus?: number
  trial?: { state: string; remaining: number; limit: number }
}) {
  const calls: Call[] = []
  const state = { errors: opts.errors ?? [] }
  const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const flowOut = { id: 'f1', name: '기본 흐름', is_public: false, deployed_version: null, updated_at: '2026-10-01T09:00:00Z', has_pending_request: false }
  let turnNo = 0
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
    calls.push({ method, path, body })
    if (method === 'GET' && path.endsWith('/api/tenant/flows/')) return json(200, opts.flows ?? [flowOut])
    if (method === 'POST' && path.endsWith('/api/tenant/flows/')) return json(201, { ...flowOut, id: 'f2', name: body.name })
    if (method === 'POST' && path.endsWith('/test/')) {
      if (opts.testStatus && opts.testStatus !== 200) return json(opts.testStatus, { detail: '권한이 없습니다.' })
      const turns = opts.testTurns ?? []
      return sse(turns[Math.min(turnNo++, turns.length - 1)] ?? [])
    }
    if (method === 'GET' && path.endsWith('/api/tenant/trial/')) return json(200, opts.trial ?? { state: 'byok', remaining: 0, limit: 20 })
    if (method === 'GET' && path.endsWith('/api/tenant/flows/node-schema/')) return json(200, { kinds: nodeSchema })
    if (method === 'GET' && /\/flows\/[^/]+\/$/.test(path)) {
      return json(200, { flow: flowOut, draft: { graph: opts.graph, errors: state.errors, valid: state.errors.length === 0 }, versions: [], pending_request: null })
    }
    if (method === 'PATCH' && path.endsWith('/draft/')) {
      if (opts.patchStatus && opts.patchStatus !== 200) return json(opts.patchStatus, opts.patchBody ?? { detail: '실패', code: 'invalid_patch', index: 0 })
      return json(200, { graph: opts.graph, errors: state.errors, valid: state.errors.length === 0 })
    }
    return json(404, { detail: 'no' })
  })
  vi.stubGlobal('fetch', fn)
  return { calls, patches: () => calls.filter((c) => c.method === 'PATCH' && c.path.endsWith('/draft/')).map((c) => c.body.ops) }
}
