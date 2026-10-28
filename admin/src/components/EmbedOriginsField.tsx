import { useEffect, useState } from 'react'
import { HttpError } from '../mutator'
import { Button } from './ui/button'

const hint = 'text-xs text-muted-foreground'

type Props = {
  origins: string[]
  /** 바꿀 수 있는가(flow.deploy, Admin). 아니면 목록만 보인다. */
  canManage: boolean
  /** 줄마다 한 출처로 나눠 보낸다. 서버가 정규화한 목록을 돌려준다. 거절은 HttpError(본문 detail을 그대로 보인다). */
  onSave: (origins: string[]) => Promise<string[]>
  /** 목록이 빌 때의 뜻(테넌트: 어디서나, 흐름: 테넌트 목록을 따름). */
  emptyHint: string
}

const parse = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)

/**
 * 허용 사이트(임베드 출처) 목록 칸(#248, ADR-0034) — 흐름 「임베드」 패널과 설정 > 보안 「내 사이트에 붙이기」 카드가 같이 쓴다.
 * 검증은 서버 한 곳(``normalize_embed_origins``)이 하고, 화면은 거절 문구를 그대로 보인다.
 */
export default function EmbedOriginsField({ origins, canManage, onSave, emptyHint }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const joined = origins.join('\n')
  const [text, setText] = useState(joined)
  useEffect(() => { setText(joined) }, [joined]) // 배열이 아니라 내용이 바뀔 때만(부모가 매번 새 [] 를 넘겨도 입력이 지워지지 않게)

  const save = async () => {
    setBusy(true)
    setError('')
    setSaved(false)
    try {
      setText((await onSave(parse(text))).join('\n'))
      setSaved(true)
    } catch (err) {
      const body = err instanceof HttpError ? (err.body as { detail?: string } | undefined) : undefined
      setError(body?.detail ?? '허용 사이트를 저장하지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-w-0 space-y-1.5">
      <p className="text-sm font-medium">허용 사이트</p>
      {canManage ? (
        <>
          <textarea aria-label="허용 사이트" rows={3} value={text} placeholder={'https://example.com\nhttps://*.example.com'}
            onChange={(e) => { setText(e.target.value); setSaved(false) }}
            className="w-full min-w-0 rounded-md border border-input bg-background px-2 py-1.5 font-mono text-xs" />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" type="button" disabled={busy} onClick={save}>{saved ? '✓ 저장됨' : '허용 사이트 저장'}</Button>
          </div>
          <p className={hint}>한 줄에 하나씩, scheme://host[:port] 모양(하위 도메인은 https://*.example.com). http는 localhost·127.0.0.1만, 20개까지.</p>
        </>
      ) : origins.length > 0 ? (
        <ul className="space-y-0.5">{origins.map((o) => <li key={o} className="break-all font-mono text-xs">{o}</li>)}</ul>
      ) : null}
      {origins.length === 0 && <p className={hint}>{emptyHint}</p>}
      <p className={hint}>다른 사이트가 iframe으로 붙이는 것만 막습니다. 주소를 직접 여는 것은 막지 않습니다.</p>
      {!canManage && <p className={hint}>허용 사이트는 관리자만 바꿀 수 있습니다.</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </div>
  )
}
