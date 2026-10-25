// 흐름 JSON(docs/flow-json.md)을 편집기가 다루는 순수 규칙. 화면·네트워크를 모른다.

export type JsonObject = Record<string, unknown>
export type FlowNodeJson = {
  id: string
  kind: string
  code: string
  name?: string
  position: { x: number; y: number }
  config: JsonObject
}
export type FlowEdgeJson = { from: string; to: string; handle?: string | null }
export type FlowGraph = { nodes: FlowNodeJson[]; edges: FlowEdgeJson[] }
export type FlowError = { node_id?: string | null; code: string; message: string }

export const DEFAULT_HANDLE = 'next'

export type KindMeta = { kind: string; label: string; hint: string }
export const KINDS: KindMeta[] = [
  { kind: 'start', label: '시작', hint: '방문자가 말을 걸면 여기서 출발' },
  { kind: 'ai_answer', label: 'AI 답변', hint: '지식을 바탕으로 답한다' },
  { kind: 'condition', label: '조건', hint: '식이 참인 갈래로 간다' },
  { kind: 'knowledge_search', label: '지식 검색', hint: '문서에서 근거를 찾는다' },
  { kind: 'ai_classify', label: 'AI 분류·추출', hint: '라벨을 고르고 값을 뽑는다' },
  { kind: 'guardrail', label: '가드레일', hint: '나가는/들어오는 말을 검사한다' },
  { kind: 'http_tool', label: 'HTTP 도구', hint: '외부 주소를 부른다' },
  { kind: 'handoff', label: '사람 넘김', hint: '상담원에게 넘기고 끝낸다' },
]
export const kindLabel = (kind: string) => KINDS.find((k) => k.kind === kind)?.label ?? kind

const DEFAULT_CONFIG: Record<string, () => JsonObject> = {
  start: () => ({}),
  ai_answer: () => ({ instructions: '', await_reply: false }),
  condition: () => ({ branches: [{ name: 'a', when: 'true' }] }),
  handoff: () => ({ reason: '' }),
  knowledge_search: () => ({ top_k: 5 }),
  ai_classify: () => ({ instructions: '', labels: ['문의'] }),
  guardrail: () => ({ rules: ['pii'], action: 'block', target: 'output' }),
  http_tool: () => ({ method: 'GET', url: 'https://example.com', headers: {}, body_template: null }),
}

/** 비어 있는 가장 작은 `N-nn` 코드. */
export function nextCode(graph: FlowGraph): string {
  const used = new Set(graph.nodes.map((n) => n.code))
  for (let i = 1; ; i++) {
    const code = `N-${String(i).padStart(2, '0')}`
    if (!used.has(code)) return code
  }
}

function nextId(graph: FlowGraph, kind: string): string {
  const used = new Set(graph.nodes.map((n) => n.id))
  if (kind === 'start' && !used.has('start')) return 'start'
  for (let i = 1; ; i++) {
    const id = `${kind}_${i}`
    if (!used.has(id)) return id
  }
}

export function newNode(graph: FlowGraph, kind: string, position: { x: number; y: number }): FlowNodeJson {
  return {
    id: nextId(graph, kind),
    kind,
    code: nextCode(graph),
    name: kindLabel(kind),
    position,
    config: (DEFAULT_CONFIG[kind] ?? (() => ({})))(),
  }
}

/** 노드에서 나갈 수 있는 갈래 이름들(순서 = 카드에 그리는 순서). */
export function outHandles(node: Pick<FlowNodeJson, 'kind' | 'config'>): string[] {
  switch (node.kind) {
    case 'handoff':
      return []
    case 'ai_answer':
      return [DEFAULT_HANDLE, 'handoff']
    case 'http_tool':
      return [DEFAULT_HANDLE, 'error'] // next=2xx, error=실패(선택, 선이 없으면 턴이 끝난다)
    case 'condition': {
      const branches = Array.isArray(node.config?.branches) ? (node.config.branches as unknown[]) : []
      const names = branches.map((b) => (b as { name?: unknown })?.name).filter((x): x is string => typeof x === 'string' && x !== '')
      return [...names, 'else']
    }
    default:
      return [DEFAULT_HANDLE]
  }
}

export function errorsByNode(errors: FlowError[]): Map<string, FlowError[]> {
  const out = new Map<string, FlowError[]>()
  for (const e of errors) {
    if (!e.node_id) continue
    out.set(e.node_id, [...(out.get(e.node_id) ?? []), e])
  }
  return out
}
export const flowLevelErrors = (errors: FlowError[]) => errors.filter((e) => !e.node_id)

export type NodeStyle = 'plain' | 'guard' | 'human'
export const nodeStyle = (kind: string): NodeStyle => (kind === 'guardrail' ? 'guard' : kind === 'handoff' ? 'human' : 'plain')

/** 카드 본문에 보일 한 줄 요약(설정을 조금 읽어 준다). */
export function nodeSummary(node: Pick<FlowNodeJson, 'kind' | 'config'>): string {
  const c = node.config ?? {}
  switch (node.kind) {
    case 'ai_answer': return typeof c.instructions === 'string' && c.instructions ? c.instructions : '기본 지침으로 답한다'
    case 'condition': return `${(Array.isArray(c.branches) ? c.branches.length : 0)}개 갈래 + else`
    case 'knowledge_search': return `상위 ${c.top_k ?? 5}건`
    case 'ai_classify': return Array.isArray(c.labels) && c.labels.length ? `라벨 ${c.labels.join('·')}` : '값 추출'
    case 'guardrail': return `${c.target === 'input' ? '들어오는 말' : '나가는 말'} · ${(Array.isArray(c.rules) ? c.rules : []).join('·')}`
    case 'http_tool': return `${c.method ?? 'GET'} ${c.url ?? ''}`
    case 'handoff': return typeof c.reason === 'string' && c.reason ? c.reason : '상담원 연결'
    default: return ''
  }
}

// ── 패치 연산(서버 apps/flows/services.py apply_ops 와 같은 뜻) ──────────────────

export type PatchOp =
  | { op: 'upsert_node'; id: string; kind?: string; code?: string; name?: string; position?: { x: number; y: number }; config?: JsonObject }
  | { op: 'delete_node'; id: string }
  | { op: 'connect'; from: string; to: string; handle?: string }
  | { op: 'disconnect'; from: string; handle?: string }

const handleOf = (e: FlowEdgeJson) => e.handle || DEFAULT_HANDLE

/** 화면에 먼저 반영하려고 연산을 로컬 그래프에 적용한다. 서버가 같은 연산을 저장하고 검증 결과를 돌려준다. */
export function applyOps(graph: FlowGraph, ops: PatchOp[]): FlowGraph {
  let nodes = graph.nodes.map((n) => ({ ...n }))
  let edges = graph.edges.map((e) => ({ ...e }))
  for (const op of ops) {
    if (op.op === 'upsert_node') {
      const { op: _o, ...fields } = op
      const at = nodes.findIndex((n) => n.id === op.id)
      if (at >= 0) nodes[at] = { ...nodes[at], ...fields } as FlowNodeJson
      else nodes.push({ name: '', position: { x: 0, y: 0 }, config: {}, ...fields } as FlowNodeJson)
    } else if (op.op === 'delete_node') {
      nodes = nodes.filter((n) => n.id !== op.id)
      edges = edges.filter((e) => e.from !== op.id && e.to !== op.id)
    } else if (op.op === 'connect') {
      const handle = op.handle ?? DEFAULT_HANDLE
      edges = edges.filter((e) => !(e.from === op.from && handleOf(e) === handle))
      edges.push(handle === DEFAULT_HANDLE ? { from: op.from, to: op.to } : { from: op.from, to: op.to, handle })
    } else {
      const handle = op.handle ?? DEFAULT_HANDLE
      edges = edges.filter((e) => !(e.from === op.from && handleOf(e) === handle))
    }
  }
  return { nodes, edges }
}

export const edgeKey = (e: FlowEdgeJson) => `${e.from}:${handleOf(e)}`

/** 시작 노드에서 `target`까지 오는 길의 선들(실행 경로 강조의 기본값). 순환이 있어도 한 번씩만 지난다. */
export function pathTo(graph: FlowGraph, target: string): { nodes: Set<string>; edges: Set<string> } {
  const start = graph.nodes.find((n) => n.kind === 'start')
  const out = { nodes: new Set<string>(), edges: new Set<string>() }
  if (!start) return out
  const prev = new Map<string, FlowEdgeJson>()
  const seen = new Set([start.id])
  const queue = [start.id]
  while (queue.length) {
    const id = queue.shift()!
    for (const e of graph.edges.filter((x) => x.from === id)) {
      if (seen.has(e.to)) continue
      seen.add(e.to)
      prev.set(e.to, e)
      queue.push(e.to)
    }
  }
  if (!seen.has(target)) return out
  let at = target
  out.nodes.add(at)
  while (prev.has(at)) {
    const e = prev.get(at)!
    out.edges.add(edgeKey(e))
    out.nodes.add(e.from)
    at = e.from
  }
  return out
}

export type Rect = { x: number; y: number; width: number; height: number }

/** 맞춤 보기: 흐름 전체가 들어오게 하되 배율이 minZoom 아래로 내려가면(노드 글자가 안 읽힌다) minZoom 에 멈추고 왼쪽 위(시작 쪽)부터 보여 준다. */
export function fitViewport(
  bounds: Rect, size: { width: number; height: number }, o: { minZoom: number; maxZoom: number; padding: number },
): { x: number; y: number; zoom: number } {
  const fit = Math.min((size.width - 2 * o.padding) / bounds.width, (size.height - 2 * o.padding) / bounds.height)
  const zoom = Math.min(o.maxZoom, Math.max(o.minZoom, fit))
  const place = (extent: number, pos: number, len: number) => {
    const free = extent - len * zoom
    return (free >= 2 * o.padding ? free / 2 : o.padding) - pos * zoom
  }
  return { x: place(size.width, bounds.x, bounds.width), y: place(size.height, bounds.y, bounds.height), zoom }
}

export type Box = { left: number; top: number; right: number; bottom: number }

/** 폰 시트가 캔버스 아래쪽을 가렸을 때(#150): 노드(화면 좌표)가 남은 영역(visible) 안에 margin 을 두고 다 보이면 null,
 *  아니면 노드를 남은 영역의 가운데로 옮길 화면 이동량. 축마다 따로 판단한다. */
export function panIntoView(node: Box, visible: Box, margin: number): { dx: number; dy: number } | null {
  const axis = (lo: number, hi: number, vlo: number, vhi: number) =>
    lo >= vlo + margin && hi <= vhi - margin ? 0 : (vlo + vhi) / 2 - (lo + hi) / 2
  const dx = axis(node.left, node.right, visible.left, visible.right) || 0
  const dy = axis(node.top, node.bottom, visible.top, visible.bottom) || 0
  return dx === 0 && dy === 0 ? null : { dx, dy }
}
