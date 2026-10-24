import { describe, it, expect } from 'vitest'
import schemas from './__fixtures__/nodeSchema.json'
import { fieldsOf, fieldErrors, withValue, type FieldSpec } from './nodeForm'

const cfg = (kind: string) => (schemas as Record<string, { config: object }>)[kind].config
const byKey = (fields: FieldSpec[]) => Object.fromEntries(fields.map((f) => [f.key, f]))

describe('fieldsOf — 백엔드 JSON 스키마에서 폼 필드를 뽑는다', () => {
  it('가드레일: 규칙은 선택지 여럿, 처리·대상은 선택지 하나, 규칙만 필수', () => {
    const f = byKey(fieldsOf(cfg('guardrail')))
    expect(f.rules).toMatchObject({ kind: 'enum_array', required: true, options: ['banned_words', 'pii', 'price_promise'] })
    expect(f.action).toMatchObject({ kind: 'enum', options: ['block', 'rewrite', 'mask'], default: 'rewrite', required: false })
    expect(f.target).toMatchObject({ kind: 'enum', options: ['output', 'input'] })
    expect(f.banned_words).toMatchObject({ kind: 'string_array' })
    expect(f.block_message).toMatchObject({ kind: 'string', required: false })
  })

  it('조건: 갈래 목록은 이름·식을 가진 객체 배열', () => {
    const f = byKey(fieldsOf(cfg('condition')))
    expect(f.branches.kind).toBe('object_array')
    expect(f.branches.required).toBe(true)
    expect(byKey(f.branches.fields!)).toMatchObject({ name: { kind: 'string', required: true }, when: { kind: 'string', required: true } })
  })

  it('AI 분류·추출: 라벨 문자열 목록과 필드(이름·설명·형식) 객체 배열', () => {
    const f = byKey(fieldsOf(cfg('ai_classify')))
    expect(f.labels.kind).toBe('string_array')
    expect(byKey(f.fields.fields!).type).toMatchObject({ kind: 'enum', options: ['string', 'number', 'boolean'] })
  })

  it('HTTP 도구: 메서드 선택, 주소 필수, 헤더는 이름-값 표, 본문은 비울 수 있는 글', () => {
    const f = byKey(fieldsOf(cfg('http_tool')))
    expect(f.method.options).toEqual(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
    expect(f.url).toMatchObject({ kind: 'string', required: true })
    expect(f.headers.kind).toBe('string_map')
    expect(f.body_template).toMatchObject({ kind: 'string', nullable: true })
  })

  it('지식 검색 top_k 는 정수와 범위, AI 답변 await_reply 는 참거짓, 시작은 필드 없음', () => {
    expect(byKey(fieldsOf(cfg('knowledge_search'))).top_k).toMatchObject({ kind: 'integer', min: 1, max: 20, default: 5 })
    expect(byKey(fieldsOf(cfg('ai_answer'))).await_reply).toMatchObject({ kind: 'boolean', default: false })
    expect(fieldsOf(cfg('start'))).toEqual([])
  })
})

describe('fieldErrors — 검증 메시지를 필드에 나눈다', () => {
  it('「경로: 이유」는 그 필드로, 경로 없는 이유는 노드 전체로 보낸다', () => {
    const r = fieldErrors([
      { node_id: 'g', code: 'invalid_config', message: 'rules: List should have at least 1 item; branches.0.when: when 식 오류: x; 가격 약속은 부분을 가릴 수 없습니다 — action을 바꾸세요' },
      { node_id: 'g', code: 'unreachable', message: '시작에서 닿지 않습니다' },
    ])
    expect(r.byPath.get('rules')).toEqual(['List should have at least 1 item'])
    expect(r.byPath.get('branches.0.when')).toEqual(['when 식 오류: x'])
    expect(r.general.map((e) => e.message)).toEqual(['가격 약속은 부분을 가릴 수 없습니다 — action을 바꾸세요', '시작에서 닿지 않습니다'])
    expect(r.general[1].code).toBe('unreachable')
  })
})

describe('withValue — config 통째 교체용 새 값', () => {
  it('값이 없거나 빈 값이면 키를 지워 기본값으로 돌리고, 모르는 키는 그대로 둔다', () => {
    expect(withValue({ a: 1, block_message: 'x' }, 'block_message', undefined)).toEqual({ a: 1 })
    expect(withValue({ a: 1 }, 'action', 'mask')).toEqual({ a: 1, action: 'mask' })
  })
})
