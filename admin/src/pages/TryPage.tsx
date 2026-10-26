import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { appsTenantsTryApiTrySessionGet, appsTenantsTryApiTryTemplates } from '../generated/endpoints/try/try'
import type { TrySessionOut, TryTemplateOut } from '../generated/model'
import { BLURB, startTry, templateId } from '@/lib/tryTemplates'
import TryShell from '../components/TryShell'
import { Button, buttonVariants } from '../components/ui/button'
import { LazyTryEditor } from './TryFlowPage'

/**
 * /try — `?template=<짧은 이름|id>` 면 그 템플릿 편집기를 곧바로 연다(방문 즉시 편집기, #195 — 세션은 첫 쓰기에 생긴다).
 * 없거나 모르는 템플릿이면 카드로 고른다: 「흐름 보기」는 읽기 전용(세션 없음), 「고쳐 보기」는 사본을 만들어 편집기로.
 */
export default function TryPage() {
  const [params] = useSearchParams()
  const picked = templateId(params.get('template'))
  const [unknown, setUnknown] = useState<string | null>(null)
  const onUnknown = useCallback(() => setUnknown(picked), [picked])
  if (picked && unknown !== picked) return <LazyTryEditor key={picked} template={picked} onUnknown={onUnknown} />
  return <TryPicker missing={unknown !== null && unknown === picked} />
}

function TryPicker({ missing }: { missing: boolean }) {
  const nav = useNavigate()
  const [params] = useSearchParams()
  const [templates, setTemplates] = useState<TryTemplateOut[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [session, setSession] = useState<TrySessionOut | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    appsTenantsTryApiTryTemplates().then((r) => setTemplates(r.data)).catch(() => setLoadError('템플릿을 불러오지 못했습니다. 잠시 뒤 다시 시도하세요.'))
    appsTenantsTryApiTrySessionGet().then((r) => setSession(r.data)).catch(() => setSession(null)) // 세션 없음은 200 null(#163) — catch 는 네트워크 오류 몫
  }, [])

  const edit = async (id: string) => {
    setBusy(id); setError('')
    try { nav(`/try/flow/${await startTry(id)}`) } catch (e) { setError((e as Error).message); setBusy(null) }
  }

  return (
    <TryShell fill>
      <div className="mx-auto w-full max-w-5xl space-y-5">
        <div className="space-y-1">
          <h1 className="font-cond text-xl font-semibold tracking-wide text-ink">흐름을 직접 고쳐 보세요</h1>
          <p className="text-sm text-muted-foreground break-keep">
            가입 없이 예제 상담 흐름의 사본을 열어 노드를 바꾸고, 시험 대화로 바로 확인합니다. 24시간 뒤 사라지고, 배포는 가입한 뒤에 할 수 있어요.
          </p>
        </div>

        {missing && <p role="alert" className="border-l-2 border-alarm pl-2 text-sm">그 템플릿을 찾지 못했어요. 아래에서 골라 주세요.</p>}
        {params.get('expired') && <p role="status" className="border-l-2 border-line-soft pl-2 text-sm">체험이 끝났거나 만료됐어요. 템플릿을 골라 새로 시작하세요.</p>}
        {session && (
          <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-ink bg-raised px-3 py-2 text-sm">
            <span>진행 중인 체험이 있어요 — 남은 대화 {session.turns_remaining}턴.</span>
            <Link to={`/try/flow/${session.flow_id}`} className={buttonVariants({ size: 'sm' }) + ' ml-auto'}>이어서 고쳐 보기</Link>
          </div>
        )}
        {error && <p role="alert" className="border-l-2 border-alarm pl-2 text-sm">{error}</p>}
        {loadError && <p role="alert" className="text-sm text-destructive">{loadError}</p>}
        {!templates && !loadError && <div aria-busy="true" />}

        <div className="grid gap-3 md:grid-cols-3">
          {templates?.map((t) => (
            <article key={t.id} data-template={t.id} className="flex min-w-0 flex-col gap-3 border border-line-soft bg-surface p-4">
              <h2 className="font-cond text-lg font-semibold text-ink">{t.name}</h2>
              <p className="text-sm text-muted-foreground break-keep">{BLURB[t.id] ?? ''}</p>
              {t.documents.length > 0 && <p className="hmi-label break-keep">지식 문서 · {t.documents.join(' · ')}</p>}
              <div className="mt-auto flex flex-wrap gap-2 pt-1">
                <Button type="button" onClick={() => edit(t.id)} disabled={busy !== null}>{busy === t.id ? '여는 중…' : '고쳐 보기'}</Button>
                <Link to={`/try/view/${t.id}`} className={buttonVariants({ variant: 'outline' })}>흐름 보기</Link>
              </div>
            </article>
          ))}
        </div>
      </div>
    </TryShell>
  )
}
