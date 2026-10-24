// 시험 대화·판단 기록의 순수 로직: SSE 조각 읽기, 노드 표시 이름, 지나간 경로, 토큰·비용 합계, 못 돈 사유 문구.
import { DEFAULT_HANDLE, edgeKey, type FlowError, type FlowGraph } from './flowGraph'

/** 실행기가 마지막에 붙이는 내부 노드(backend executor.FINISH_NODE). 캔버스에는 없다. */
export const FINISH_NODE = '__flow_finish__'

export type TraceCall = { model: string; input_tokens: number; output_tokens: number; cost_usd: string | null }
export type TraceEntry = {
  node_id: string
  kind: string
  handle: string
  record: Record<string, unknown>
  duration_ms: number
  input_tokens: number
  output_tokens: number
  cost_usd: string | null
  calls: TraceCall[]
}
export type TestTurn = {
  n: number
  message: string
  reply: string
  entries: TraceEntry[]
  ran: boolean
  handoff: boolean
  note: string
  errors: FlowError[]
  failure: string // 연결·권한 같은 전송 실패 사유
  pending: boolean
}
export type SseEvent = { event: string; data: any } // eslint-disable-line @typescript-eslint/no-explicit-any -- 이벤트마다 모양이 달라 호출부(reduce)에서 좁힌다

/** 버퍼에서 완성된 SSE 이벤트를 꺼내고 모자란 끝(`rest`)을 돌려준다. */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const parts = buffer.replace(/\r\n/g, '\n').split('\n\n')
  const rest = parts.pop() ?? ''
  const events: SseEvent[] = []
  for (const block of parts) {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    if (data.length === 0) continue
    try { events.push({ event, data: JSON.parse(data.join('\n')) }) } catch { /* 깨진 조각은 건너뛴다 */ }
  }
  return { events, rest }
}

/** 판단 기록 항목의 이름. 내부 끝 노드는 「끝」, 흐름 노드는 `N-코드 이름`. */
export function entryLabel(e: Pick<TraceEntry, 'node_id'>, graph: FlowGraph): string {
  if (e.node_id === FINISH_NODE) return '끝'
  const n = graph.nodes.find((x) => x.id === e.node_id)
  return n ? `${n.code} ${n.name}` : e.node_id
}

/** 한 턴이 지나간 노드 id 집합과 선 키(`from:handle`) 집합 — 캔버스 강조용. */
export function turnPath(entries: TraceEntry[]): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set<string>()
  const edges = new Set<string>()
  for (const e of entries) {
    if (e.node_id === FINISH_NODE) continue
    nodes.add(e.node_id)
    edges.add(edgeKey({ from: e.node_id, to: '', handle: e.handle || DEFAULT_HANDLE }))
  }
  return { nodes, edges }
}

export const formatTokens = (input: number, output: number) => `${input}/${output}`

export function formatCost(cost: string | null, hasUsage: boolean): string {
  if (cost === null) return hasUsage ? '단가 미상' : '—'
  return `$${Number(cost).toFixed(4)}`
}

export type Totals = { input: number; output: number; cost: string | null; duration: number; costKnown: boolean }

const SCALE = 10
const toUnits = (s: string): bigint => {
  const [i, f = ''] = s.split('.')
  return BigInt(i + f.padEnd(SCALE, '0').slice(0, SCALE))
}
function fromUnits(u: bigint): string {
  const s = u.toString().padStart(SCALE + 1, '0')
  const frac = s.slice(-SCALE).replace(/0+$/, '').padEnd(4, '0')
  return `${s.slice(0, -SCALE)}.${frac}`
}

export function turnTotals(entries: TraceEntry[]): Totals {
  let input = 0, output = 0, duration = 0, units = 0n
  let costKnown = true
  for (const e of entries) {
    input += e.input_tokens
    output += e.output_tokens
    duration += e.duration_ms
    if (e.cost_usd === null) { if (e.input_tokens || e.output_tokens) costKnown = false } else units += toUnits(e.cost_usd)
  }
  return { input, output, duration, costKnown, cost: costKnown ? fromUnits(units) : null }
}

export const sessionTotals = (turns: Pick<TestTurn, 'entries'>[]): Totals => turnTotals(turns.flatMap((t) => t.entries))

/** `ran=false` 일 때 패널에 보일 사람이 읽는 문구. `trial`은 체험 상태(/trial), 모르면 null. */
export function notRunMessage(errors: FlowError[], note: string, trial: string | null): string {
  if (errors.length > 0) return '초안에 오류가 있어 돌리지 못했습니다. 초안을 고친 뒤 다시 시험하세요.'
  if (trial === 'exhausted') return '체험 턴을 모두 썼습니다. 설정에서 내 LLM 키를 넣으면 계속 시험할 수 있습니다.'
  if (trial === 'unavailable') return '이 서버에는 플랫폼 키가 없어 체험으로는 시험할 수 없습니다. 설정에서 내 LLM 키를 넣어 주세요.'
  return note || '시험을 돌리지 못했습니다.'
}


/** 저장된 판단 기록(GET /sessions/{id}/decision-traces)을 화면의 턴으로 되돌린다. 방문자 말·답은 시작·답변 노드 기록에서 읽는다. */
export function restoredTurns(stored: { turn: number; entries: unknown[] }[]): TestTurn[] {
  const text = (v: unknown) => (typeof v === 'string' ? v : '')
  return stored.map((t) => {
    const entries = t.entries as TraceEntry[]
    const message = text(entries.map((e) => e.record?.input).find((v) => typeof v === 'string' && v))
    const reply = text(entries.filter((e) => e.kind === 'ai_answer').map((e) => e.record?.output).filter(Boolean).pop())
    return { n: t.turn, message, reply, entries, ran: true, handoff: entries.some((e) => e.kind === 'handoff'), note: '', errors: [], failure: '', pending: false }
  })
}
