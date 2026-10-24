import { describe, it, expect } from 'vitest'
import {
  parseSse, entryLabel, turnPath, formatCost, formatTokens, turnTotals, sessionTotals, notRunMessage,
  FINISH_NODE, type TraceEntry, type TestTurn,
} from './testTrace'
import type { FlowGraph } from './flowGraph'

const GRAPH: FlowGraph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} },
    { id: 'ai1', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 1, y: 0 }, config: {} },
  ],
  edges: [{ from: 'start', to: 'ai1' }],
}
const entry = (o: Partial<TraceEntry>): TraceEntry => ({
  node_id: 'start', kind: 'start', handle: 'next', record: {}, duration_ms: 3, input_tokens: 0, output_tokens: 0, cost_usd: null, calls: [], ...o,
})

describe('parseSse', () => {
  it('완성된 이벤트만 꺼내고 모자란 끝은 남긴다', () => {
    const a = parseSse('event: start\ndata: {"flow_id":"f"}\n\nevent: reply\ndata: {"te')
    expect(a.events).toEqual([{ event: 'start', data: { flow_id: 'f' } }])
    const b = parseSse(a.rest + 'xt":"안녕"}\n\n')
    expect(b.events).toEqual([{ event: 'reply', data: { text: '안녕' } }])
    expect(b.rest).toBe('')
  })
  it('CRLF 구분도 읽는다', () => {
    expect(parseSse('event: done\r\ndata: {"ran":true}\r\n\r\n').events[0].event).toBe('done')
  })
})

describe('entryLabel', () => {
  it('흐름 노드는 N-코드와 이름, 내부 끝 노드는 「끝」, 모르는 id는 id 그대로', () => {
    expect(entryLabel(entry({ node_id: 'ai1' }), GRAPH)).toBe('N-02 답변')
    expect(entryLabel(entry({ node_id: FINISH_NODE }), GRAPH)).toBe('끝')
    expect(entryLabel(entry({ node_id: 'gone' }), GRAPH)).toBe('gone')
  })
})

describe('turnPath', () => {
  it('지나간 노드와 선(from:handle)을 모으되 내부 끝 노드는 캔버스에 없으니 뺀다', () => {
    const p = turnPath([entry({ node_id: 'start' }), entry({ node_id: 'ai1', handle: 'next' }), entry({ node_id: FINISH_NODE, handle: '' })])
    expect([...p.nodes].sort()).toEqual(['ai1', 'start'])
    expect([...p.edges].sort()).toEqual(['ai1:next', 'start:next'])
  })
  it('handle이 비면 next', () => {
    expect([...turnPath([entry({ handle: '' })]).edges]).toEqual(['start:next'])
  })
})

describe('비용·토큰 표기', () => {
  it('비용은 소수 넷째 자리 달러, 토큰이 있는데 단가를 모르면 단가 미상, 쓴 게 없으면 대시', () => {
    expect(formatCost('0.000123', true)).toBe('$0.0001')
    expect(formatCost('0.0123', true)).toBe('$0.0123')
    expect(formatCost(null, true)).toBe('단가 미상')
    expect(formatCost(null, false)).toBe('—')
    expect(formatCost('0', false)).toBe('$0.0000')
  })
  it('토큰은 입력/출력', () => {
    expect(formatTokens(120, 30)).toBe('120/30')
  })
})

describe('합계', () => {
  const t1 = [entry({ input_tokens: 100, output_tokens: 20, cost_usd: '0.0010', duration_ms: 50 }), entry({ node_id: 'ai1', duration_ms: 7 })]
  const t2 = [entry({ input_tokens: 10, output_tokens: 5, cost_usd: '0.0002', duration_ms: 20 })]
  it('턴 합계', () => {
    expect(turnTotals(t1)).toEqual({ input: 100, output: 20, cost: '0.0010', duration: 57, costKnown: true })
  })
  it('모르는 단가가 하나라도 토큰을 썼으면 비용은 null', () => {
    const t = turnTotals([...t1, entry({ input_tokens: 5, output_tokens: 1, cost_usd: null })])
    expect(t.cost).toBeNull()
    expect(t.costKnown).toBe(false)
  })
  it('세션 합계는 턴 합계의 합', () => {
    const turns = [{ entries: t1 }, { entries: t2 }] as unknown as TestTurn[]
    expect(sessionTotals(turns)).toEqual({ input: 110, output: 25, cost: '0.0012', duration: 77, costKnown: true })
  })
})

describe('notRunMessage', () => {
  it('초안 오류가 있으면 고치라고, 체험 상태에 따라 한도·키 없음 문구', () => {
    expect(notRunMessage([{ node_id: 'x', code: 'c', message: 'm' }], '', 'trial')).toMatch(/초안/)
    expect(notRunMessage([], '체험…', 'exhausted')).toMatch(/체험 턴을 모두 썼/)
    expect(notRunMessage([], '체험…', 'unavailable')).toMatch(/플랫폼 키/)
    expect(notRunMessage([], '서버 사유', null)).toBe('서버 사유')
  })
})
