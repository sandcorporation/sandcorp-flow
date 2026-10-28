import { describe, expect, it } from 'vitest'
import type { LiveNode, NodeEndData } from './liveRun'
import { formatDuration, nodeBadge } from './nodeBadge'

const end = (o: Partial<NodeEndData> = {}): NodeEndData => ({
  node_id: 'a', kind: 'ai_answer', step: 1, handle: 'default', duration_ms: 800, input_tokens: 0, output_tokens: 0,
  cost_usd: null, unpriced_models: [], summary: null, ...o,
})
const node = (o: Partial<LiveNode> = {}, e: Partial<NodeEndData> | null = {}): LiveNode => ({
  status: 'done', kind: 'ai_answer', step: 1, count: 1, end: e === null ? undefined : end(e), ...o,
})

describe('formatDuration', () => {
  it('1초 아래는 ms, 그 위는 초', () => {
    expect(formatDuration(120)).toBe('120ms')
    expect(formatDuration(1234)).toBe('1.2초')
    expect(formatDuration(12_400)).toBe('12초')
  })
})

describe('nodeBadge', () => {
  it('끝나지 않았거나 오류거나 없는 노드는 배지가 없다', () => {
    expect(nodeBadge(undefined)).toBeNull()
    expect(nodeBadge(node({ status: 'running' }, null))).toBeNull()
    expect(nodeBadge(node({ status: 'error', message: '실패' }))).toBeNull()
  })
  it('토큰이 없는 노드는 시간과 summary 만 보인다', () => {
    const b = nodeBadge(node({}, { summary: '갈래 \'환불\'' }))!
    expect(b).toMatchObject({ time: '800ms', tokens: null, cost: null, summary: "갈래 '환불'", times: null })
    expect(b.text).toBe("800ms · 갈래 '환불'")
  })
  it('토큰과 비용을 보인다', () => {
    const b = nodeBadge(node({}, { input_tokens: 120, output_tokens: 30, cost_usd: '0.0010', summary: '답 42자' }))!
    expect(b.tokens).toBe('120/30')
    expect(b.cost).toBe('$0.0010')
    expect(b.text).toBe('800ms · 토큰 120/30 · $0.0010 · 답 42자')
  })
  it('쓴 토큰이 있는데 비용이 null 이면 0원이 아니라 「단가 모름」(모델 이름 포함)', () => {
    const b = nodeBadge(node({}, { input_tokens: 10, output_tokens: 5, cost_usd: null, unpriced_models: ['gpt-x'] }))!
    expect(b.cost).toBe('단가 모름 (모델 gpt-x)')
  })
  it('아는 호출만 합친 값이면 일부 미포함을 덧붙인다', () => {
    const b = nodeBadge(node({}, { input_tokens: 10, output_tokens: 5, cost_usd: '0.0020', unpriced_models: ['m2'] }))!
    expect(b.cost).toBe('$0.0020 · 일부 미포함 (모델 m2)')
  })
  it('토큰도 비용도 없으면(0 원 포함) 비용 칸을 비운다', () => {
    expect(nodeBadge(node({}, { cost_usd: '0.000000' }))!.cost).toBeNull()
  })
  it('같은 노드를 여러 번 지났으면 ×N 을 앞에 붙인다', () => {
    const b = nodeBadge(node({ count: 3 }))!
    expect(b.times).toBe('×3')
    expect(b.text).toBe('×3 · 800ms')
  })
  it('summary 가 비었거나 공백이면 뺀다', () => {
    expect(nodeBadge(node({}, { summary: '  ' }))!.summary).toBeNull()
  })
})
