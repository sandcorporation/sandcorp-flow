import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { appsTenantsTryApiTrySessionGet } from '../generated/endpoints/try/try'
import { FlowEditorBackendProvider, lazyTryBackend, tryBackend, tryViewBackend, type FlowEditorBackend } from '@/lib/flowEditorBackend'
import { shortTemplate, startTry } from '@/lib/tryTemplates'
import { claimLoginUrl } from '../api'
import { outboundProps, useEmbed } from '@/lib/embed'
import { TRY_SESSION_LOST } from '../mutator'
import { TRIAL_CHANGED } from '../components/TrialBanner'
import FlowEditorPage from '../components/FlowEditorPage'
import TryShell from '../components/TryShell'
import { Button } from '../components/ui/button'

/**
 * 「이 흐름으로 가입」(#42): 고객 로그인(sandcorp-auth) 뒤 옮기기 화면(/try/claim)으로 돌아와 사본을 내 테넌트로 옮긴다.
 * 세션이 아직 없으면(#195) 중간 페이지(/try/signup, #198)로 간다 — 거기서 세션을 만든 뒤 로그인으로 넘어간다.
 * 임베드는 새 탭을 동기적으로 열어야 하므로(비동기 뒤 window.open 은 팝업 차단) 링크 자체가 중간 페이지를 가리킨다.
 */
function SignupLink({ template }: { template?: string }) {
  const embed = useEmbed() // 임베드(#180)에서는 iframe 을 떠나지 않고 새 탭으로
  const cls = 'whitespace-nowrap'
  return (
    <Button asChild variant="head" size="sm">
      {template
        ? <Link to={`/try/signup?template=${encodeURIComponent(shortTemplate(template))}`} className={cls} {...outboundProps(embed)}>이 흐름으로 가입</Link>
        : <a href={claimLoginUrl()} className={cls} {...outboundProps(embed)}>이 흐름으로 가입</a>}
    </Button>
  )
}

/** 체험 편집기의 겉틀: 머리줄(남은 대화·가입) + 편집기. 세션이 끊기면(401 try_no_session) 템플릿 고르기로 돌려보낸다. */
function TryEditorShell({ backend, left, signup, onTrialChanged, notice, stayOnLost }: { backend: FlowEditorBackend; left: number | null; signup: ReactNode; onTrialChanged: () => void; notice?: ReactNode; stayOnLost?: boolean }) {
  const nav = useNavigate()
  useEffect(() => {
    const lost = () => nav('/try?expired=1', { replace: true })
    window.addEventListener(TRIAL_CHANGED, onTrialChanged) // 시험 대화 턴이 끝날 때마다
    if (!stayOnLost) window.addEventListener(TRY_SESSION_LOST, lost) // 쿠키가 없거나 만료 — 새 편집기(#197)는 튕기지 않고 지역 모드로 안내한다
    return () => { window.removeEventListener(TRIAL_CHANGED, onTrialChanged); window.removeEventListener(TRY_SESSION_LOST, lost) }
  }, [onTrialChanged, nav, stayOnLost])

  const right = (
    <>
      {left !== null && <span className="hmi-label flex items-center gap-1.5 !text-head-foreground" role="status"><span className={`lamp ${left > 0 ? 'lamp-ok' : 'lamp-alarm'}`} />남은 대화 {left}턴</span>}
      {signup}
    </>
  )
  return (
    <TryShell right={right} fill>
      {notice}
      <FlowEditorBackendProvider value={backend}>
        <FlowEditorPage />
      </FlowEditorBackendProvider>
    </TryShell>
  )
}

/**
 * 방문 즉시 편집기(#195): `/try?template=…` 가 곧바로 그리는 편집기. 진입은 템플릿·노드 틀·세션 읽기만 하고,
 * 세션은 첫 쓰기에 생긴다(`lazyTryBackend`). 같은 템플릿의 세션이 살아 있으면 그 사본을 연다(주소는 그대로).
 */
export function LazyTryEditor({ template, onUnknown }: { template: string; onUnknown: () => void }) {
  const [left, setLeft] = useState<number | null>(null)
  const [notice, setNotice] = useState<'other' | 'expired' | null>(null)
  const backend = useMemo(
    () => lazyTryBackend(template, {
      onSession: (s) => { setLeft(s.turns_remaining); setNotice(null) },
      onUnknown,
      onOtherSession: () => setNotice((n) => n ?? 'other'),
      onExpired: () => { setLeft(null); setNotice('expired') },
    }),
    [template, onUnknown],
  )
  const refresh = useCallback(() => {
    appsTenantsTryApiTrySessionGet().then((r) => { if (r.data?.template === template) setLeft(r.data.turns_remaining) }).catch(() => {})
  }, [template])
  const note = notice === 'expired'
    ? '체험 시간이 지나 세션이 끝났어요. 지금까지 고친 내용은 이 화면에 그대로 있고, 다음에 저장하거나 시험하면 새 체험으로 이어서 시작해요.'
    : notice === 'other'
      ? '다른 템플릿으로 진행 중인 체험이 있어요. 여기서 고치면 새 체험으로 바뀝니다.'
      : ''
  return (
    <TryEditorShell
      backend={backend} left={left} onTrialChanged={refresh} stayOnLost
      notice={note && <p role="status" data-testid="try-notice" className="mb-3 border-l-2 border-line-soft pl-2 text-sm">{note}</p>}
      signup={<SignupLink template={left === null ? template : undefined} />}
    />
  )
}

/** /try/flow/:id(예전 고쳐 보기 주소 — 열린 탭·북마크 호환) · /try/view/:id(흐름 보기: 템플릿 원본, 읽기 전용). */
export default function TryFlowPage({ view = false }: { view?: boolean }) {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const [left, setLeft] = useState<number | null>(null)
  const [startError, setStartError] = useState('')
  const [starting, setStarting] = useState(false)

  const refresh = useCallback(() => {
    appsTenantsTryApiTrySessionGet().then((r) => { if (r.data) setLeft(r.data.turns_remaining) }).catch(() => {})
  }, [])
  useEffect(() => { if (!view) refresh() }, [view, refresh])

  if (!view) return <TryEditorShell backend={tryBackend} left={left} onTrialChanged={refresh} signup={<SignupLink />} />

  const edit = async () => {
    setStarting(true); setStartError('')
    try { nav(`/try/flow/${await startTry(id)}`) } catch (e) { setStartError((e as Error).message) } finally { setStarting(false) }
  }
  return (
    <TryShell right={<Button variant="head" size="sm" onClick={edit} disabled={starting}>고쳐 보기</Button>} fill>
      {startError && <p role="alert" className="mb-3 text-sm text-destructive">{startError}</p>}
      <FlowEditorBackendProvider value={tryViewBackend}>
        <FlowEditorPage />
      </FlowEditorBackendProvider>
    </TryShell>
  )
}
