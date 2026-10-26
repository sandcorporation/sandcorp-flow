import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { appsTenantsTryApiTrySessionGet } from '../generated/endpoints/try/try'
import { FlowEditorBackendProvider, lazyTryBackend, tryBackend, tryViewBackend, type FlowEditorBackend } from '@/lib/flowEditorBackend'
import { startTry, TRY_ERRORS } from '@/lib/tryTemplates'
import { claimLoginUrl } from '../api'
import { outboundProps, useEmbed } from '@/lib/embed'
import { HttpError, TRY_SESSION_LOST } from '../mutator'
import { TRIAL_CHANGED } from '../components/TrialBanner'
import FlowEditorPage from '../components/FlowEditorPage'
import TryShell from '../components/TryShell'
import { Button } from '../components/ui/button'

/**
 * 「이 흐름으로 가입」(#42): 고객 로그인(sandcorp-auth) 뒤 옮기기 화면(/try/claim)으로 돌아와 사본을 내 테넌트로 옮긴다.
 * 세션이 아직 없으면(방문 즉시 편집기, #195) `ensure` 로 세션을 먼저 만든 뒤 로그인으로 간다. 임베드는 새 탭을 동기적으로 열어야 해서
 * (비동기 뒤 window.open 은 팝업 차단) 세션 없이는 링크를 막고 안내만 한다 — #198 이 중간 페이지(/try/signup)로 바꾼다.
 */
function SignupLink({ ensure }: { ensure?: () => Promise<unknown> }) {
  const embed = useEmbed() // 임베드(#180)에서는 iframe 을 떠나지 않고 새 탭으로
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (!ensure) return <Button asChild variant="head" size="sm"><a href={claimLoginUrl()} className="whitespace-nowrap" {...outboundProps(embed)}>이 흐름으로 가입</a></Button>
  if (embed) return <span className="hmi-label !text-head-foreground" role="note">흐름을 한 번 고치면 가입할 수 있어요</span>
  const go = async () => {
    setBusy(true); setError('')
    try { await ensure(); window.location.assign(claimLoginUrl()) } catch (e) { setError(tryFailure(e)); setBusy(false) }
  }
  return (
    <>
      {error && <span role="alert" className="text-xs">{error}</span>}
      <Button variant="head" size="sm" className="whitespace-nowrap" onClick={go} disabled={busy}>이 흐름으로 가입</Button>
    </>
  )
}

const tryFailure = (e: unknown): string => {
  const body = e instanceof HttpError ? (e.body as { detail?: string; code?: string } | undefined) : undefined
  return body?.detail || (body?.code && TRY_ERRORS[body.code]) || '체험을 시작하지 못했습니다. 잠시 뒤 다시 시도하세요.'
}

/** 체험 편집기의 겉틀: 머리줄(남은 대화·가입) + 편집기. 세션이 끊기면(401 try_no_session) 템플릿 고르기로 돌려보낸다. */
function TryEditorShell({ backend, left, signup, onTrialChanged }: { backend: FlowEditorBackend; left: number | null; signup: ReactNode; onTrialChanged: () => void }) {
  const nav = useNavigate()
  useEffect(() => {
    const lost = () => nav('/try?expired=1', { replace: true })
    window.addEventListener(TRIAL_CHANGED, onTrialChanged) // 시험 대화 턴이 끝날 때마다
    window.addEventListener(TRY_SESSION_LOST, lost) // 쿠키가 없거나 만료
    return () => { window.removeEventListener(TRIAL_CHANGED, onTrialChanged); window.removeEventListener(TRY_SESSION_LOST, lost) }
  }, [onTrialChanged, nav])

  const right = (
    <>
      {left !== null && <span className="hmi-label flex items-center gap-1.5 !text-head-foreground" role="status"><span className={`lamp ${left > 0 ? 'lamp-ok' : 'lamp-alarm'}`} />남은 대화 {left}턴</span>}
      {signup}
    </>
  )
  return (
    <TryShell right={right} fill>
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
  const backend = useMemo(
    () => lazyTryBackend(template, { onSession: (s) => setLeft(s.turns_remaining), onUnknown }),
    [template, onUnknown],
  )
  const refresh = useCallback(() => {
    appsTenantsTryApiTrySessionGet().then((r) => { if (r.data?.template === template) setLeft(r.data.turns_remaining) }).catch(() => {})
  }, [template])
  return <TryEditorShell backend={backend} left={left} onTrialChanged={refresh} signup={<SignupLink ensure={left === null ? backend.ensureSession : undefined} />} />
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
