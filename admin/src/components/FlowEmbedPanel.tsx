import { useState } from 'react'
import { appsFlowsApiFlowsSetDefault, appsFlowsApiFlowsSetEmbed, appsFlowsApiFlowsSetEmbedOrigins } from '../generated/endpoints/flows/flows'
import type { FlowOut } from '../generated/model'
import { HttpError } from '../mutator'
import { buildKeyIframeCode } from './EmbedGuideCard'
import EmbedOriginsField from './EmbedOriginsField'
import { Button } from './ui/button'

const hint = 'text-xs text-muted-foreground'

type Props = {
  flow: FlowOut
  /** 배포 중인 버전이 있는가(편집기에서는 방금 배포한 것까지 반영한 값). */
  deployed: boolean
  /** 켬/끔·기본 흐름 바꾸기 권한(flow.deploy, Admin). 코드 복사·미리보기는 권한과 상관없다. */
  canManage: boolean
  origin: string
  onChange: (flow: FlowOut) => void
}

/**
 * 흐름 하나의 「임베드」 패널 — 켬/끔 · iframe 코드 복사 · 미리보기 · 「기본 흐름으로」(#247, ADR-0034).
 * 흐름 목록 행과 편집기 머리가 같이 쓴다. 허용 사이트 칸(#248)은 이 흐름의 키 주소에만 쓰이고, 비면 테넌트 목록을 따른다.
 */
export default function FlowEmbedPanel({ flow, deployed, canManage, origin, onChange }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const open = flow.embed_enabled || flow.is_public // 기본 흐름은 꺼도 자기 키 주소로 열려 있다
  const code = flow.embed_path ? buildKeyIframeCode(origin, flow.embed_path) : ''
  const toggleDisabled = !canManage || busy || (!deployed && !flow.embed_enabled)

  const run = async (call: () => Promise<{ data: unknown }>, fallback: string) => {
    setBusy(true)
    setError('')
    try {
      onChange((await call()).data as FlowOut)
    } catch (err) {
      const body = err instanceof HttpError ? (err.body as { detail?: string } | undefined) : undefined
      setError(body?.detail ?? fallback)
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard 거부 시 무시 */ }
  }

  return (
    <section aria-label={`${flow.name} 임베드`} className="min-w-0 space-y-2 rounded-lg border border-border bg-card p-3 text-left">
      <label className={`flex items-center gap-2 text-sm ${toggleDisabled ? 'opacity-60' : 'cursor-pointer'}`}>
        <input type="checkbox" className="h-4 w-4 rounded border-input" aria-label="임베드 켜기" checked={flow.embed_enabled} disabled={toggleDisabled}
          onChange={(e) => run(() => appsFlowsApiFlowsSetEmbed(flow.id, { enabled: e.target.checked }), '임베드를 바꾸지 못했습니다.')} />
        임베드 켜기
      </label>
      {!deployed && !flow.embed_enabled && <p className={hint}>아직 배포한 버전이 없어 켤 수 없습니다. 먼저 배포하세요.</p>}
      {flow.is_public && <p className={hint}>기본 흐름은 임베드를 꺼도 열려 있습니다(기본 주소와 이 키 주소 모두).</p>}
      {open && code ? (
        <>
          <pre data-testid="flow-embed-code" tabIndex={0} className="max-w-full overflow-x-auto rounded bg-muted p-2 text-xs leading-relaxed">{code}</pre>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" type="button" onClick={copy}>{copied ? '✓ 복사됨' : '코드 복사'}</Button>
            <a href={`${origin}${flow.embed_path}`} target="_blank" rel="noopener noreferrer"
              className="text-sm text-primary underline-offset-4 hover:underline">미리보기 열기 ↗</a>
          </div>
        </>
      ) : (
        <p className={hint}>임베드를 켜면 붙일 코드가 나타납니다.</p>
      )}
      <EmbedOriginsField origins={flow.embed_origins ?? []} canManage={canManage} emptyHint="비어 있음 — 테넌트 목록을 따름(설정 > 보안)."
        onSave={async (origins) => {
          const next = (await appsFlowsApiFlowsSetEmbedOrigins(flow.id, { origins })).data as FlowOut
          onChange(next)
          return next.embed_origins ?? []
        }} />
      {canManage && !flow.is_public && (
        <Button size="sm" variant="outline" type="button" disabled={busy || !deployed}
          onClick={() => run(() => appsFlowsApiFlowsSetDefault(flow.id), '기본 흐름을 바꾸지 못했습니다.')}>기본 흐름으로</Button>
      )}
      {canManage && !flow.is_public && <p className={hint}>기본 흐름으로 바꾸면 위젯 기본 주소가 이 흐름으로 돕니다. 지금의 기본 흐름은 해제됩니다. 열려 있던 대화는 끊기지 않습니다.</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </section>
  )
}
