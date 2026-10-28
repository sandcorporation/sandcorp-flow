// 시험 대화 실시간 노드 상태의 순수 규칙(#240): SSE node_start/node_end/node_error 를 노드별 상태·지나간 선으로 접는다.
// 캔버스가 이 상태를 그리고, 노드 배지(#241)는 `end` 값을 읽는다.
import { DEFAULT_HANDLE, edgeKey } from './flowGraph'
import { FINISH_NODE, type SseEvent, type TraceEntry, entryPricing } from './testTrace'

export type LiveStatus = 'running' | 'done' | 'error'

/** `node_end` 이벤트가 실어 보내는 값(배지용으로 그대로 보관한다). */
export type NodeEndData = {
  node_id: string
  kind: string
  step: number
  handle: string
  duration_ms: number
  input_tokens: number
  output_tokens: number
  cost_usd: string | null
  unpriced_models: string[]
  summary: string | null
}

export type LiveNode = {
  status: LiveStatus
  kind: string
  step: number // 마지막으로 시작·끝난 턴 안 순번
  count: number // 이 턴에 이 노드를 지난(시작한) 횟수
  end?: NodeEndData // 마지막 node_end 값(없으면 아직 안 끝났거나 오류)
  message?: string // node_error 문구
}

export type LiveRun = {
  nodes: Readonly<Record<string, LiveNode>>
  edges: readonly string[] // 지나간 선 키(`from:handle`)
  finished: boolean // done 이 왔다
}

export const emptyLiveRun: LiveRun = { nodes: {}, edges: [], finished: false }

type Raw = Record<string, unknown>
const str = (v: unknown, d = ''): string => (typeof v === 'string' ? v : d)
const num = (v: unknown): number => (typeof v === 'number' ? v : 0)

/** 이벤트 하나를 상태에 더한 새 상태. 상관없는 이벤트는 같은 객체를 돌려준다. */
export function applyLiveEvent(run: LiveRun, { event, data }: SseEvent): LiveRun {
  const d = (data ?? {}) as Raw
  if (event === 'done') return { ...run, finished: true }
  if (event !== 'node_start' && event !== 'node_end' && event !== 'node_error') return run
  const id = str(d.node_id)
  if (!id || id === FINISH_NODE) return run
  const prev = run.nodes[id]
  const base = { kind: str(d.kind), step: num(d.step) }
  if (event === 'node_start') {
    return { ...run, nodes: { ...run.nodes, [id]: { ...base, status: 'running', count: (prev?.count ?? 0) + 1, end: prev?.end, message: undefined } } }
  }
  if (event === 'node_error') {
    return { ...run, nodes: { ...run.nodes, [id]: { ...base, status: 'error', count: prev?.count ?? 1, end: prev?.end, message: str(d.message) } } }
  }
  const handle = str(d.handle) || DEFAULT_HANDLE
  const end: NodeEndData = {
    node_id: id, ...base, handle,
    duration_ms: num(d.duration_ms), input_tokens: num(d.input_tokens), output_tokens: num(d.output_tokens),
    cost_usd: typeof d.cost_usd === 'string' ? d.cost_usd : null,
    unpriced_models: Array.isArray(d.unpriced_models) ? d.unpriced_models.map(String) : [],
    summary: typeof d.summary === 'string' ? d.summary : null,
  }
  const key = edgeKey({ from: id, to: '', handle })
  return {
    ...run,
    nodes: { ...run.nodes, [id]: { ...base, status: 'done', count: Math.max(prev?.count ?? 0, 1), end } },
    edges: run.edges.includes(key) ? run.edges : [...run.edges, key],
  }
}

export const liveStatusOf = (run: LiveRun, id: string): LiveStatus | null => run.nodes[id]?.status ?? null
export const liveEdgeKeys = (run: LiveRun): ReadonlySet<string> => new Set(run.edges)
export const liveIsEmpty = (run: LiveRun): boolean => Object.keys(run.nodes).length === 0

/** 저장된 판단 기록(마지막 턴)에서 배지용 상태를 되살린다(새로고침 복원, #241). 저장 기록엔 summary 가 없어 null — 시간·토큰·비용만 보인다. */
export function liveRunFromEntries(entries: readonly TraceEntry[]): LiveRun {
  let run: LiveRun = emptyLiveRun
  entries.forEach((e, i) => {
    if (e.node_id === FINISH_NODE) return
    const price = entryPricing(e)
    const common = { node_id: e.node_id, kind: e.kind, step: i + 1 }
    run = applyLiveEvent(run, { event: 'node_start', data: common })
    run = applyLiveEvent(run, {
      event: 'node_end',
      data: { ...common, handle: e.handle, duration_ms: e.duration_ms, input_tokens: e.input_tokens, output_tokens: e.output_tokens, cost_usd: price.known, unpriced_models: price.unpriced },
    })
  })
  return entries.length > 0 ? { ...run, finished: true } : run
}
