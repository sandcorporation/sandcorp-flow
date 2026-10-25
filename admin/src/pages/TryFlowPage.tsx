import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { appsTenantsTryApiTrySessionGet } from '../generated/endpoints/try/try'
import { FlowEditorBackendProvider, tryBackend, tryViewBackend } from '@/lib/flowEditorBackend'
import { startTry } from '@/lib/tryTemplates'
import { TRY_SESSION_LOST } from '../mutator'
import { TRIAL_CHANGED } from '../components/TrialBanner'
import FlowEditorPage from '../components/FlowEditorPage'
import TryShell from '../components/TryShell'
import { Button } from '../components/ui/button'

/** 가입 이식(#42)은 아직 — 자리만 둔다. */
function SignupSlot() {
  return <Button variant="head" size="sm" disabled title="곧 열립니다">이 흐름으로 가입</Button>
}

/** /try/flow/:id(고쳐 보기: 편집기 + 시험 대화) · /try/view/:id(흐름 보기: 템플릿 원본, 읽기 전용). */
export default function TryFlowPage({ view = false }: { view?: boolean }) {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const [left, setLeft] = useState<number | null>(null)
  const [startError, setStartError] = useState('')
  const [starting, setStarting] = useState(false)

  const refresh = useCallback(() => {
    appsTenantsTryApiTrySessionGet().then((r) => setLeft(r.data.turns_remaining)).catch(() => {})
  }, [])

  useEffect(() => {
    if (view) return
    refresh()
    const lost = () => nav('/try?expired=1', { replace: true })
    window.addEventListener(TRIAL_CHANGED, refresh) // 시험 대화 턴이 끝날 때마다
    window.addEventListener(TRY_SESSION_LOST, lost) // 쿠키가 없거나 만료
    return () => { window.removeEventListener(TRIAL_CHANGED, refresh); window.removeEventListener(TRY_SESSION_LOST, lost) }
  }, [view, refresh, nav])

  const edit = async () => {
    setStarting(true); setStartError('')
    try { nav(`/try/flow/${await startTry(id)}`) } catch (e) { setStartError((e as Error).message) } finally { setStarting(false) }
  }

  const right = view ? (
    <>
      <Button variant="head" size="sm" onClick={edit} disabled={starting}>고쳐 보기</Button>
      <SignupSlot />
    </>
  ) : (
    <>
      {left !== null && <span className="hmi-label flex items-center gap-1.5 !text-head-foreground" role="status"><span className={`lamp ${left > 0 ? 'lamp-ok' : 'lamp-alarm'}`} />남은 대화 {left}턴</span>}
      <SignupSlot />
    </>
  )

  return (
    <TryShell right={right} fill>
      {startError && <p role="alert" className="mb-3 text-sm text-destructive">{startError}</p>}
      <FlowEditorBackendProvider value={view ? tryViewBackend : tryBackend}>
        <FlowEditorPage />
      </FlowEditorBackendProvider>
    </TryShell>
  )
}
