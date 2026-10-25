import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import type { JsonObject } from '@/lib/flowGraph'
import { fieldsOf, withValue, type FieldSpec } from '@/lib/nodeForm'
import { FIELD_TEXT, GUARDRAIL_WARNING, HTTP_LIVE_NOTICE, KIND_NOTE, WHEN_KEYS, headerNeedsSecretRef } from '@/lib/nodeFormLabels'
import { hostAllowed, isDemoApiUrl, secretNamesUsed, urlHost } from '@/lib/httpToolWarn'
import { listAllowedDomains, listSecrets } from '../api'
import { useFlowEditorBackend } from '@/lib/flowEditorBackend'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Select } from './ui/select'
import { Textarea } from './ui/textarea'

type Ctx = { readOnly: boolean; kind: string; errors: Map<string, string[]>; after?: Record<string, ReactNode> }

const SETTINGS_LINK = '/tenant/config?section=secrets'

const textOf = (kind: string, path: string) => FIELD_TEXT[`${kind}.${path.replace(/\.\d+/g, '')}`] ?? { label: path.split('.').pop() ?? path }

/** 종류별 설정 폼. 필드 구성은 백엔드 스키마에서, 라벨은 nodeFormLabels 에서. 값을 바꾸면 config 통째로 onChange 에 넘긴다. */
export default function NodeConfigForm({ kind, schema, config, readOnly, errors, onChange }: {
  kind: string; schema: object; config: JsonObject; readOnly: boolean; errors: Map<string, string[]>; onChange: (next: JsonObject) => void
}) {
  const fields = fieldsOf(schema)
  const { tenantLookups } = useFlowEditorBackend()
  const lookups = useHttpLookups(kind === 'http_tool' && tenantLookups)
  const ctx: Ctx = { readOnly, kind, errors, after: kind === 'http_tool' ? { url: <HostWarning url={typeof config.url === 'string' ? config.url : ''} domains={lookups.domains} /> } : undefined }
  return (
    <div className="space-y-3">
      {kind === 'guardrail' && (
        <p className="border-l-4 border-hazard bg-hazard/10 px-2 py-1.5 text-sm text-ink">{GUARDRAIL_WARNING}</p>
      )}
      {kind === 'http_tool' && <p className="border-l-4 border-hazard bg-hazard/10 px-2 py-1.5 text-sm text-ink">{HTTP_LIVE_NOTICE}</p>}
      {(fields.length === 0 || kind === 'http_tool') && KIND_NOTE[kind] && <p className="text-sm text-muted-foreground">{KIND_NOTE[kind]}</p>}
      {kind === 'condition' && <WhenKeys />}
      {fields.map((f) => (
        <FieldView key={f.key} spec={f} path={f.key} value={config[f.key]} ctx={ctx} onChange={(v) => onChange(withValue(config, f.key, v))} />
      ))}
      {kind === 'http_tool' && <SecretsStatus used={secretNamesUsed(config)} secrets={lookups.secrets} />}
    </div>
  )
}

function WhenKeys() {
  return (
    <div className="border border-line-soft bg-raised p-2 text-xs text-muted-foreground">
      <p className="mb-1">조건식에서 읽을 수 있는 값</p>
      <ul className="space-y-0.5">
        {WHEN_KEYS.map((k) => <li key={k.name}><code className="hmi-code text-ink">{k.name}</code> — {k.help}</li>)}
      </ul>
    </div>
  )
}

/** 허용 도메인·비밀 목록(이름만). 못 읽으면(권한·네트워크) null — 그러면 경고를 내지 않는다. 설정 탭에서 돌아오면(창 포커스) 다시 읽는다. */
function useHttpLookups(enabled: boolean) {
  const [domains, setDomains] = useState<string[] | null>(null)
  const [secrets, setSecrets] = useState<string[] | null>(null)
  const load = useCallback(() => {
    let live = true
    Promise.resolve().then(() => listAllowedDomains()).then((d) => live && setDomains(d.map((x) => x.pattern))).catch(() => live && setDomains(null))
    Promise.resolve().then(() => listSecrets()).then((d) => live && setSecrets(d.map((x) => x.name))).catch(() => live && setSecrets(null))
    return () => { live = false }
  }, [])
  useEffect(() => {
    if (!enabled) return
    let stop = load()
    const again = () => { stop(); stop = load() }
    window.addEventListener('focus', again)
    return () => { stop(); window.removeEventListener('focus', again) }
  }, [enabled, load])
  return { domains, secrets }
}

/** 주소 호스트가 허용 도메인에 없으면 알린다(서버 AllowList 와 같은 규칙, 최종 판정은 서버). */
function HostWarning({ url, domains }: { url: string; domains: string[] | null }) {
  if (isDemoApiUrl(url)) {
    return (
      <p role="status" className="border-l-4 border-ink/30 bg-ink/5 px-2 py-1.5 text-xs text-ink">
        예제용 <b>가짜 API</b> — 허용 도메인 없이 호출되고, 실제 네트워크로 나가지 않고 정해진 규칙대로 응답합니다. 배포 전 실제 주소로 바꾸세요.
      </p>
    )
  }
  const host = urlHost(url)
  if (domains === null || host === null || hostAllowed(host, domains)) return null
  return (
    <p role="status" className="border-l-4 border-hazard bg-hazard/10 px-2 py-1.5 text-xs text-ink">
      <b className="hmi-code">{host}</b> 은(는) 허용 도메인에 없습니다 — 이대로는 호출이 막힙니다(host_not_allowed).{' '}
      <a className="underline" href={SETTINGS_LINK} target="_blank" rel="noreferrer">설정에서 허용 도메인 추가</a>
    </p>
  )
}

/** 등록된 비밀 이름과, 쓰는데 등록 안 된 참조. 값은 어디서도 읽지 않는다. */
function SecretsStatus({ used, secrets }: { used: string[]; secrets: string[] | null }) {
  if (secrets === null) return null
  const missing = used.filter((n) => !secrets.includes(n))
  return (
    <div className="space-y-1.5 border border-dashed border-line-soft p-2 text-xs text-muted-foreground">
      {secrets.length === 0
        ? <p>등록된 비밀이 없습니다. 헤더·본문에는 {'{{secret.이름}}'} 꼴로만 참조합니다.</p>
        : <p>등록된 비밀: {secrets.map((n, i) => <span key={n}>{i > 0 && ', '}<code className="hmi-code text-ink">{n}</code></span>)}</p>}
      {missing.length > 0 && (
        <p role="status" className="border-l-4 border-hazard bg-hazard/10 px-2 py-1 text-ink">
          등록되지 않은 비밀: {missing.map((n, i) => <span key={n}>{i > 0 && ', '}<code className="hmi-code">{n}</code></span>)} — 저장 검증에서 막힙니다(unknown_secret).
        </p>
      )}
      <a className="underline" href={SETTINGS_LINK} target="_blank" rel="noreferrer">설정에서 비밀·허용 도메인 관리</a>
    </div>
  )
}

function FieldView({ spec, path, value, ctx, onChange }: { spec: FieldSpec; path: string; value: unknown; ctx: Ctx; onChange: (v: unknown) => void }) {
  const text = textOf(ctx.kind, path)
  const errs = ctx.errors.get(path) ?? []
  const id = `f-${ctx.kind}-${path}`
  const err = errs.length > 0 ? <p id={`${id}-err`} className="text-xs text-destructive">{errs.join(' · ')}</p> : null
  const help = text.help ? <p className="text-xs text-muted-foreground">{text.help}</p> : null
  const bad = errs.length > 0
  const common = { 'aria-invalid': bad || undefined, 'aria-describedby': bad ? `${id}-err` : undefined } as const

  switch (spec.kind) {
    case 'string':
      return (
        <div className="space-y-1">
          <Label htmlFor={id}>{text.label}</Label>
          <DraftText id={id} value={typeof value === 'string' ? value : ''} readOnly={ctx.readOnly} placeholder={text.placeholder} multiline={path.endsWith('instructions') || path.endsWith('body_template')}
            invalid={bad} describedBy={common['aria-describedby']}
            onCommit={(s) => onChange(s === '' && !spec.required ? undefined : s)} />
          {help}{err}{ctx.after?.[path]}
        </div>
      )
    case 'integer':
      return (
        <div className="space-y-1">
          <Label htmlFor={id}>{text.label}</Label>
          <DraftText id={id} type="number" min={spec.min} max={spec.max} value={value === undefined ? '' : String(value)} readOnly={ctx.readOnly} invalid={bad} describedBy={common['aria-describedby']}
            onCommit={(s) => { const n = Number.parseInt(s, 10); onChange(Number.isNaN(n) ? undefined : n) }} />
          {help}{err}
        </div>
      )
    case 'boolean':
      return (
        <div className="space-y-1">
          <label htmlFor={id} className="flex items-start gap-2 text-sm">
            <input id={id} type="checkbox" className="mt-1" checked={Boolean(value ?? spec.default)} disabled={ctx.readOnly} onChange={(e) => onChange(e.target.checked)} />
            <span>{text.label}</span>
          </label>
          {help}{err}
        </div>
      )
    case 'enum': {
      const current = typeof value === 'string' ? value : typeof spec.default === 'string' ? spec.default : spec.options[0]
      return (
        <div className="space-y-1">
          <Label htmlFor={id}>{text.label}</Label>
          <Select id={id} value={current} disabled={ctx.readOnly} onChange={(e) => onChange(e.target.value)} {...common}>
            {spec.options.map((o) => <option key={o} value={o}>{text.options?.[o] ?? o}</option>)}
          </Select>
          {help}{err}
        </div>
      )
    }
    case 'enum_array': {
      const picked = Array.isArray(value) ? (value as string[]) : []
      return (
        <fieldset className="space-y-1" aria-describedby={bad ? `${id}-err` : undefined}>
          <legend className="text-sm font-medium">{text.label}</legend>
          {spec.options.map((o) => (
            <label key={o} className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={picked.includes(o)} disabled={ctx.readOnly}
                onChange={(e) => onChange(e.target.checked ? [...picked, o] : picked.filter((x) => x !== o))} />
              <span>{text.options?.[o] ?? o}</span>
            </label>
          ))}
          {help}{err}
        </fieldset>
      )
    }
    case 'string_array': {
      const lines = Array.isArray(value) ? (value as string[]) : []
      return (
        <div className="space-y-1">
          <Label htmlFor={id}>{text.label}</Label>
          <DraftText id={id} multiline value={lines.join('\n')} readOnly={ctx.readOnly} invalid={bad} describedBy={common['aria-describedby']}
            onCommit={(s) => {
              const next = s.split('\n').map((l) => l.trim()).filter(Boolean)
              onChange(next.length === 0 && !spec.required ? undefined : next)
            }} />
          {help}{err}
        </div>
      )
    }
    case 'object_array': {
      const rows = Array.isArray(value) ? (value as JsonObject[]) : []
      const blank: JsonObject = Object.fromEntries((spec.fields ?? []).filter((f) => f.required).map((f) => [f.key, '']))
      return (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{text.label}</legend>
          {help}
          {rows.map((row, i) => (
            <div key={i} className="space-y-2 border border-line-soft bg-raised p-2">
              {(spec.fields ?? []).map((f) => (
                <FieldView key={f.key} spec={f} path={`${path}.${i}.${f.key}`} value={row[f.key]} ctx={ctx}
                  onChange={(v) => onChange(rows.map((r, j) => (j === i ? withValue(r, f.key, v) : r)))} />
              ))}
              {!ctx.readOnly && (
                <Button type="button" size="sm" variant="outline" aria-label={`${text.label} ${i + 1}번 삭제`} onClick={() => onChange(rows.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" />삭제</Button>
              )}
            </div>
          ))}
          {!ctx.readOnly && <Button type="button" size="sm" variant="outline" onClick={() => onChange([...rows, { ...blank }])}><Plus className="h-4 w-4" />{text.add ?? '추가'}</Button>}
          {err}
        </fieldset>
      )
    }
    case 'string_map':
      return <HeaderRows path={path} text={text} value={(value && typeof value === 'object' ? value : {}) as Record<string, string>} ctx={ctx} err={err} help={help} onChange={onChange} />
  }
}

/** 글 입력: 쓰는 동안은 화면에만, 포커스를 잃을 때 한 번 저장한다. 바깥 값이 바뀌면(서버 되맞춤 등) 따라간다. */
function DraftText({ value, onCommit, multiline, readOnly, id, invalid, describedBy, type, min, max, placeholder }: {
  value: string; onCommit: (s: string) => void; multiline?: boolean; readOnly: boolean; id: string; invalid: boolean; describedBy?: string
  type?: string; min?: number; max?: number; placeholder?: string
}) {
  const [text, setText] = useState(value)
  useEffect(() => setText(value), [value])
  const commit = () => { if (!readOnly && text !== value) onCommit(text) }
  const shared = { id, value: text, readOnly, placeholder, 'aria-invalid': invalid || undefined, 'aria-describedby': describedBy, onBlur: commit }
  return multiline
    ? <Textarea {...shared} className="min-h-[72px] w-full" onChange={(e) => setText(e.target.value)} />
    : <Input {...shared} type={type} min={min} max={max} onChange={(e) => setText(e.target.value)} />
}

/** 헤더 표(이름-값). 비밀 원문이 들어가면 저장 전에 참조를 쓰라고 알린다(최종 판정은 서버 검증). */
function HeaderRows({ path, text, value, ctx, err, help, onChange }: {
  path: string; text: { label: string; add?: string; keyLabel?: string; valueLabel?: string; keyPlaceholder?: string; valuePlaceholder?: string }; value: Record<string, string>; ctx: Ctx; err: React.ReactNode; help: React.ReactNode; onChange: (v: unknown) => void
}) {
  const fromProps = () => Object.entries(value)
  const [rows, setRows] = useState<[string, string][]>(fromProps)
  const sent = useRef(JSON.stringify(value))
  const key = JSON.stringify(value)
  useEffect(() => { if (key !== sent.current) { sent.current = key; setRows(Object.entries(value)) } }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const commit = (next: [string, string][]) => {
    const obj = Object.fromEntries(next.filter(([k]) => k.trim() !== '').map(([k, v]) => [k.trim(), v]))
    sent.current = JSON.stringify(obj)
    onChange(Object.keys(obj).length === 0 ? undefined : obj)
  }
  const isHeaders = path === 'headers'
  const keyLabel = text.keyLabel ?? '헤더 이름'
  const valueLabel = text.valueLabel ?? '헤더 값'
  const itemName = isHeaders ? '헤더' : text.add?.replace(/ 추가$/, '') ?? text.label
  const edit = (i: number, k: string, v: string) => setRows(rows.map((r, j): [string, string] => (j === i ? [k, v] : r)))
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{text.label}</legend>
      {help}
      {rows.map(([k, v], i) => (
        <div key={i} className="space-y-1 border border-line-soft bg-raised p-2">
          <Label htmlFor={`${path}-k-${i}`}>{keyLabel}</Label>
          <Input id={`${path}-k-${i}`} placeholder={text.keyPlaceholder} value={k} readOnly={ctx.readOnly} onChange={(e) => edit(i, e.target.value, v)} onBlur={() => !ctx.readOnly && commit(rows)} />
          <Label htmlFor={`${path}-v-${i}`}>{valueLabel}</Label>
          <Input id={`${path}-v-${i}`} value={v} readOnly={ctx.readOnly} onChange={(e) => edit(i, k, e.target.value)} onBlur={() => !ctx.readOnly && commit(rows)}
            placeholder={isHeaders ? (headerNeedsSecretRef(k, '') ? 'Bearer {{secret.이름}}' : '') : text.valuePlaceholder} aria-invalid={(isHeaders && headerNeedsSecretRef(k, v)) || undefined} />
          {isHeaders && headerNeedsSecretRef(k, v) && <p className="text-xs text-destructive">인증 헤더에는 비밀 원문 대신 {'{{secret.이름}}'} 참조를 넣으세요. (예: Bearer {'{{secret.shop_key}}'})</p>}
          {!ctx.readOnly && <Button type="button" size="sm" variant="outline" aria-label={`${itemName} ${i + 1}번 삭제`} onClick={() => { const next = rows.filter((_, j) => j !== i); setRows(next); commit(next) }}><Trash2 className="h-4 w-4" />삭제</Button>}
        </div>
      ))}
      {!ctx.readOnly && <Button type="button" size="sm" variant="outline" onClick={() => setRows([...rows, ['', '']])}><Plus className="h-4 w-4" />{text.add ?? '추가'}</Button>}
      {err}
    </fieldset>
  )
}
