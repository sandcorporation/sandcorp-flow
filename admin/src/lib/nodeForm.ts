// 노드 설정 폼의 틀: 백엔드 pydantic 모델이 낸 JSON 스키마(GET /api/tenant/flows/node-schema/)에서 필드·선택지·필수·범위·기본값을
// 뽑는다. 사람이 읽을 라벨·도움말은 화면 쪽(nodeFormLabels.ts)에만 둔다. 화면·네트워크를 모른다.
import type { FlowError, JsonObject } from './flowGraph'

type Schema = {
  type?: string
  enum?: unknown[]
  default?: unknown
  minimum?: number
  maximum?: number
  anyOf?: Schema[]
  items?: Schema
  properties?: Record<string, Schema>
  required?: string[]
  additionalProperties?: Schema | boolean
  $ref?: string
  $defs?: Record<string, Schema>
}

export type FieldKind = 'string' | 'integer' | 'boolean' | 'enum' | 'enum_array' | 'string_array' | 'object_array' | 'string_map'

export type FieldSpec = {
  key: string
  kind: FieldKind
  required: boolean
  nullable: boolean
  default?: unknown
  options: string[]
  min?: number
  max?: number
  fields?: FieldSpec[] // object_array 의 한 줄이 가진 필드
}

function resolve(s: Schema, defs: Record<string, Schema>): Schema {
  if (s.$ref) return resolve(defs[s.$ref.replace('#/$defs/', '')] ?? {}, defs)
  return s
}

function specOf(key: string, raw: Schema, required: boolean, defs: Record<string, Schema>): FieldSpec | null {
  let s = resolve(raw, defs)
  let nullable = false
  if (s.anyOf) {
    const rest = s.anyOf.filter((x) => x.type !== 'null')
    nullable = rest.length !== s.anyOf.length
    if (rest.length !== 1) return null
    s = resolve({ ...rest[0], default: s.default }, defs)
  }
  const base = { key, required, nullable, default: s.default, options: [] as string[] }
  if (s.enum) return { ...base, kind: 'enum', options: s.enum.map(String) }
  switch (s.type) {
    case 'string': return { ...base, kind: 'string' }
    case 'boolean': return { ...base, kind: 'boolean' }
    case 'integer': return { ...base, kind: 'integer', min: s.minimum, max: s.maximum }
    case 'object':
      return typeof s.additionalProperties === 'object' ? { ...base, kind: 'string_map' } : null
    case 'array': {
      const item = resolve(s.items ?? {}, defs)
      if (item.enum) return { ...base, kind: 'enum_array', options: item.enum.map(String) }
      if (item.type === 'string') return { ...base, kind: 'string_array' }
      if (item.type === 'object') return { ...base, kind: 'object_array', fields: fieldsOf(item, defs) }
      return null
    }
    default: return null
  }
}

/** 스키마의 최상위 속성들을 폼 필드로(스키마 순서 그대로). 모양을 모르는 속성은 건너뛴다 — 「고급」 JSON 탭에서 고친다. */
export function fieldsOf(schema: object, parentDefs?: Record<string, Schema>): FieldSpec[] {
  const s = schema as Schema
  const defs = { ...parentDefs, ...s.$defs }
  const required = new Set(s.required ?? [])
  return Object.entries(s.properties ?? {})
    .map(([key, p]) => specOf(key, p, required.has(key), defs))
    .filter((f): f is FieldSpec => f !== null)
}

/** 검증 메시지 「경로: 이유; 경로: 이유」를 경로(예 `branches.0.when`)별로 나눈다. 경로 없는 것과 invalid_config 가 아닌 것은 general. */
export function fieldErrors(errors: FlowError[]): { byPath: Map<string, string[]>; general: FlowError[] } {
  const byPath = new Map<string, string[]>()
  const general: FlowError[] = []
  for (const e of errors) {
    if (e.code !== 'invalid_config') { general.push(e); continue }
    for (const part of e.message.split('; ')) {
      const m = /^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*): (.+)$/s.exec(part)
      if (m) byPath.set(m[1], [...(byPath.get(m[1]) ?? []), m[2]])
      else general.push({ ...e, message: part })
    }
  }
  return { byPath, general }
}

/** config 의 한 키를 바꾼 새 config. undefined 면 키를 지워 기본값으로 돌린다. 모르는 키는 보존한다. */
export function withValue(config: JsonObject, key: string, value: unknown): JsonObject {
  const next = { ...config }
  if (value === undefined) delete next[key]
  else next[key] = value
  return next
}
