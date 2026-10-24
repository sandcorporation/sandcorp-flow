import { describe, it, expect } from 'vitest'
import { newNode, outHandles, errorsByNode, nextCode, nodeStyle, flowLevelErrors, applyOps, pathTo, edgeKey, type FlowGraph , fitViewport } from './flowGraph'

const g = (nodes: FlowGraph['nodes'] = [], edges: FlowGraph['edges'] = []): FlowGraph => ({ nodes, edges })
const n = (id: string, kind: string, code: string, config = {}) => ({ id, kind, code, name: '', position: { x: 0, y: 0 }, config })

describe('flowGraph — 편집기 순수 규칙', () => {
  it('새 노드의 N-코드는 비어 있는 가장 작은 번호이고 id는 겹치지 않는다', () => {
    const graph = g([n('start', 'start', 'N-01'), n('ai_answer_1', 'ai_answer', 'N-03')])
    const a = newNode(graph, 'ai_answer', { x: 10, y: 20 })
    expect(a.code).toBe('N-02')
    expect(a.id).toBe('ai_answer_2')
    expect(a.position).toEqual({ x: 10, y: 20 })
    expect(nextCode(g())).toBe('N-01')
  })

  it('새 노드의 기본 config는 종류별 검증을 통과하는 모양이다', () => {
    const graph = g()
    expect(newNode(graph, 'guardrail', { x: 0, y: 0 }).config).toMatchObject({ rules: ['pii'], action: 'block' })
    expect(newNode(graph, 'condition', { x: 0, y: 0 }).config).toMatchObject({ branches: [{ name: 'a', when: 'true' }] })
    expect(newNode(graph, 'knowledge_search', { x: 0, y: 0 }).config).toEqual({ top_k: 5 })
  })

  it('나가는 갈래는 종류와 설정에서 나온다', () => {
    expect(outHandles(n('s', 'start', 'N-01'))).toEqual(['next'])
    expect(outHandles(n('h', 'handoff', 'N-02'))).toEqual([])
    expect(outHandles(n('a', 'ai_answer', 'N-03'))).toEqual(['next', 'handoff'])
    expect(outHandles(n('t', 'http_tool', 'N-05'))).toEqual(['next', 'error'])
    expect(outHandles(n('c', 'condition', 'N-04', { branches: [{ name: '환불', when: 'x' }, { name: '배송', when: 'y' }] })))
      .toEqual(['환불', '배송', 'else'])
  })

  it('검증 오류를 노드 id별로 묶고 흐름 전체 오류는 따로 돌려준다', () => {
    const errors = [
      { node_id: 'a', code: 'invalid_config', message: 'x' },
      { node_id: 'a', code: 'unreachable_node', message: 'y' },
      { node_id: null, code: 'start_count', message: 'z' },
    ]
    expect(errorsByNode(errors).get('a')).toHaveLength(2)
    expect(flowLevelErrors(errors)).toEqual([errors[2]])
  })

  it('노드 모양은 종류에서: 가드레일=경고띠, 사람 넘김=적색', () => {
    expect(nodeStyle('guardrail')).toBe('guard')
    expect(nodeStyle('handoff')).toBe('human')
    expect(nodeStyle('start')).toBe('plain')
  })

  it('패치 연산을 서버와 같은 뜻으로 로컬에 적용한다', () => {
    const base = g([n('a', 'start', 'N-01'), n('b', 'ai_answer', 'N-02'), n('c', 'handoff', 'N-03')], [{ from: 'a', to: 'b' }])
    const moved = applyOps(base, [{ op: 'upsert_node', id: 'b', position: { x: 5, y: 6 } }])
    expect(moved.nodes[1].position).toEqual({ x: 5, y: 6 })
    expect(moved.nodes[1].kind).toBe('ai_answer')
    const rewired = applyOps(base, [{ op: 'connect', from: 'a', to: 'c' }, { op: 'connect', from: 'b', to: 'c', handle: 'handoff' }])
    expect(rewired.edges).toEqual([{ from: 'a', to: 'c' }, { from: 'b', to: 'c', handle: 'handoff' }])
    expect(applyOps(rewired, [{ op: 'disconnect', from: 'b', handle: 'handoff' }]).edges).toEqual([{ from: 'a', to: 'c' }])
    const removed = applyOps(base, [{ op: 'delete_node', id: 'b' }])
    expect(removed.nodes.map((x) => x.id)).toEqual(['a', 'c'])
    expect(removed.edges).toEqual([])
    expect(base.nodes).toHaveLength(3) // 원본은 바뀌지 않는다
  })

  it('시작에서 고른 노드까지 오는 길을 찾는다', () => {
    const graph = g(
      [n('s', 'start', 'N-01'), n('a', 'ai_answer', 'N-02'), n('b', 'ai_answer', 'N-03'), n('x', 'handoff', 'N-04')],
      [{ from: 's', to: 'a' }, { from: 'a', to: 'b' }, { from: 'a', to: 'x', handle: 'handoff' }],
    )
    const p = pathTo(graph, 'b')
    expect([...p.nodes].sort()).toEqual(['a', 'b', 's'])
    expect([...p.edges].sort()).toEqual([edgeKey({ from: 's', to: 'a' }), edgeKey({ from: 'a', to: 'b' })].sort())
    expect(pathTo(graph, 'zzz').edges.size).toBe(0)
  })
})

describe('fitViewport — 맞춤 보기는 글자가 읽히는 배율 아래로 줄이지 않는다', () => {
  const opts = { minZoom: 0.75, maxZoom: 1, padding: 24 }
  it('작은 흐름은 화면 가운데에 크기 1로 둔다', () => {
    const v = fitViewport({ x: 0, y: 0, width: 400, height: 200 }, { width: 1000, height: 600 }, opts)
    expect(v).toEqual({ x: 300, y: 200, zoom: 1 })
  })
  it('넓은 흐름은 배율을 minZoom 에 멈추고 왼쪽 위(시작 쪽)에서 보여 준다', () => {
    const v = fitViewport({ x: -100, y: -50, width: 2000, height: 400 }, { width: 800, height: 600 }, opts)
    expect(v.zoom).toBe(0.75)
    expect(v.x).toBe(24 + 100 * 0.75)
    expect(v.y).toBe((600 - 400 * 0.75) / 2 + 50 * 0.75)
  })
  it('minZoom 이상으로 들어가면 화면에 딱 맞춘다', () => {
    const v = fitViewport({ x: 0, y: 0, width: 1000, height: 100 }, { width: 800, height: 600 }, { ...opts, minZoom: 0.1 })
    expect(v.zoom).toBeCloseTo((800 - 48) / 1000)
  })
})
