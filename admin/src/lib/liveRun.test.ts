import { describe, expect, it } from 'vitest'
import { applyLiveEvent, emptyLiveRun, liveEdgeKeys, liveStatusOf } from './liveRun'

const end = (id: string, step: number, handle = 'next', extra = {}) => ({
  event: 'node_end',
  data: { node_id: id, kind: 'ai_answer', step, handle, duration_ms: 12, input_tokens: 3, output_tokens: 4, cost_usd: '0.0010', unpriced_models: [], summary: '답 12자', ...extra },
})
const start = (id: string, step: number) => ({ event: 'node_start', data: { node_id: id, kind: 'ai_answer', step } })

describe('liveRun', () => {
  it('node_start 는 실행 중, node_end 는 끝남이고 값은 보관한다', () => {
    let r = applyLiveEvent(emptyLiveRun, start('a', 0))
    expect(liveStatusOf(r, 'a')).toBe('running')
    r = applyLiveEvent(r, end('a', 0))
    expect(liveStatusOf(r, 'a')).toBe('done')
    expect(r.nodes.a.end).toMatchObject({ step: 0, handle: 'next', duration_ms: 12, input_tokens: 3, output_tokens: 4, cost_usd: '0.0010', summary: '답 12자' })
    expect(r.nodes.a.count).toBe(1)
  })

  it('지나간 선은 node_end 의 handle 로 만든다', () => {
    let r = applyLiveEvent(emptyLiveRun, end('a', 0, 'next'))
    r = applyLiveEvent(r, end('b', 1, 'refund'))
    expect([...liveEdgeKeys(r)].sort()).toEqual(['a:next', 'b:refund'])
  })

  it('같은 노드를 다시 지나면 마지막 값과 횟수를 쓴다', () => {
    let r = applyLiveEvent(emptyLiveRun, end('a', 0, 'x', { duration_ms: 1 }))
    r = applyLiveEvent(r, start('a', 3))
    expect(liveStatusOf(r, 'a')).toBe('running')
    r = applyLiveEvent(r, end('a', 3, 'x', { duration_ms: 9 }))
    expect(r.nodes.a.count).toBe(2)
    expect(r.nodes.a.end?.duration_ms).toBe(9)
  })

  it('node_error 는 오류와 문구를 남기고 턴이 끝나도 지워지지 않는다', () => {
    let r = applyLiveEvent(emptyLiveRun, start('a', 0))
    r = applyLiveEvent(r, { event: 'node_error', data: { node_id: 'a', kind: 'ai_answer', step: 0, message: '모델 호출 실패' } })
    expect(liveStatusOf(r, 'a')).toBe('error')
    expect(r.nodes.a.message).toBe('모델 호출 실패')
    r = applyLiveEvent(r, { event: 'done', data: { ran: false, code: 'turn_error' } })
    expect(liveStatusOf(r, 'a')).toBe('error')
    expect(r.finished).toBe(true)
  })

  it('done 에서 끝났음을 표시하고, 모르는 이벤트와 내부 끝 노드는 무시한다', () => {
    let r = applyLiveEvent(emptyLiveRun, { event: 'reply', data: { text: 'x' } })
    expect(r).toBe(emptyLiveRun)
    r = applyLiveEvent(r, end('__flow_finish__', 2))
    expect(Object.keys(r.nodes)).toEqual([])
    r = applyLiveEvent(r, { event: 'done', data: { ran: true } })
    expect(r.finished).toBe(true)
  })

  it('아직 안 돈 노드는 상태가 없다', () => {
    expect(liveStatusOf(emptyLiveRun, 'zzz')).toBeNull()
  })
})
