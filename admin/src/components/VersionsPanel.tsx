import { useState } from 'react'
import { Eye, RotateCcw, Rocket } from 'lucide-react'
import { appsFlowsApiFlowsDeploy, appsFlowsApiFlowsRollback, appsFlowsApiFlowsSaveVersion } from '../generated/endpoints/flows/flows'
import type { DeployRequestOut, VersionOut } from '../generated/model'
import { HttpError } from '../mutator'
import { deployAction, needsTestConfirm, rollbackState } from '@/lib/deploy'
import DeployRequestCard, { fmtTime, type Me } from './DeployRequestCard'
import { Button } from './ui/button'
import { Input } from './ui/input'

/** 왼쪽 아래 「버전 이력」: 저장(note) · 보기 · 배포(또는 배포 요청) · 되돌리기 · 이 흐름의 승인 대기 요청. */
export default function VersionsPanel({ flowId, versions, deployedVersion, pending, requiresApproval, canDeploy, me, testedTurns, viewing, onView, onChanged }: {
  flowId: string
  versions: VersionOut[]
  deployedVersion: number | null
  pending: DeployRequestOut | null
  requiresApproval: boolean
  canDeploy: boolean // flow.deploy·flow.approve 는 같은 Admin 판정
  me: Me
  testedTurns: number
  viewing: number | null
  onView: (n: number) => void
  onChanged: () => void
}) {
  const [note, setNote] = useState('')
  const [confirming, setConfirming] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const run = async (job: () => Promise<string | void>, fail: string) => {
    setBusy(true)
    setError('')
    setInfo('')
    try {
      const msg = await job()
      if (msg) setInfo(msg)
      onChanged()
    } catch (err) {
      const body = err instanceof HttpError ? (err.body as { detail?: string } | undefined) : undefined
      setError(body?.detail ?? fail)
    } finally {
      setBusy(false)
    }
  }

  const saveVersion = () => run(async () => {
    const r = await appsFlowsApiFlowsSaveVersion(flowId, { note: note.trim() || null })
    setNote('')
    return (r.data as unknown as { created: boolean }).created ? '' : '초안이 마지막 버전과 같아 새 버전을 만들지 않았습니다.'
  }, '버전을 저장하지 못했습니다.')

  const deploy = (version: number) => {
    setConfirming(null)
    return run(async () => {
      const r = await appsFlowsApiFlowsDeploy(flowId, { version })
      return (r.data as unknown as { deployed: boolean }).deployed ? '' : `v${version} 배포를 요청했습니다. 다른 관리자의 승인을 기다립니다.`
    }, '배포하지 못했습니다.')
  }
  const askDeploy = (version: number) => (needsTestConfirm(testedTurns) ? setConfirming(version) : deploy(version))
  const rollback = () => run(async () => { await appsFlowsApiFlowsRollback(flowId) }, '되돌리지 못했습니다.')

  const rb = rollbackState(versions, deployedVersion)
  const rbDisabled = busy || !canDeploy || rb.disabled
  const rbReason = !canDeploy ? '되돌리기는 관리자만 할 수 있습니다.' : rb.reason

  return (
    <section aria-label="버전 이력" className="min-w-0 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="hmi-label">버전 이력</h2>
        <span title={rbReason}>
          <Button type="button" size="sm" variant="outline" className="h-7 px-2" disabled={rbDisabled} onClick={rollback}>
            <RotateCcw className="h-3.5 w-3.5" />되돌리기
          </Button>
        </span>
      </div>
      {canDeploy && (
        <form className="space-y-1.5" onSubmit={(e) => { e.preventDefault(); void saveVersion() }}>
          <Input aria-label="버전 메모" value={note} onChange={(e) => setNote(e.target.value)} placeholder="이 버전의 메모(선택)" maxLength={500} />
          <Button type="submit" size="sm" variant="secondary" className="w-full" disabled={busy}>버전 저장</Button>
        </form>
      )}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      {info && <p role="status" className="text-xs text-muted-foreground">{info}</p>}
      {pending && <DeployRequestCard request={pending} canApprove={canDeploy} me={me} onDecided={onChanged} />}
      {confirming !== null && (
        <div role="group" aria-label="배포 확인" className="space-y-1.5 border border-alarm bg-raised p-2 text-sm">
          <p>시험 대화 없이 v{confirming}을(를) {requiresApproval ? '요청' : '배포'}합니다. 시험해 보지 않은 흐름이 방문자 대화에 적용될 수 있습니다.</p>
          <div className="flex flex-wrap gap-1.5">
            <Button type="button" size="sm" disabled={busy} onClick={() => deploy(confirming)}>{requiresApproval ? '그래도 배포 요청' : '그래도 배포'}</Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setConfirming(null)}>취소</Button>
          </div>
        </div>
      )}
      {versions.length === 0 ? (
        <p className="text-xs text-muted-foreground">아직 저장한 버전이 없습니다. 시험한 초안을 「버전 저장」하면 배포할 수 있습니다.</p>
      ) : (
        <ul className="space-y-1.5">
          {[...versions].sort((a, b) => b.number - a.number).map((v) => {
            const act = deployAction({ canDeploy, requiresApproval, version: v.number, deployedVersion, pendingVersion: pending?.version ?? null })
            return (
              <li key={v.number} data-version={v.number} data-deployed={v.number === deployedVersion ? 'true' : undefined}
                className={`space-y-1 border bg-raised p-2 text-sm ${viewing === v.number ? 'border-ink' : 'border-line-soft'}`}>
                <div className="flex items-center gap-1.5">
                  <span className="hmi-code font-semibold text-ink">v{v.number}</span>
                  {v.number === deployedVersion && <span className="hmi-label flex items-center gap-1"><span className="lamp lamp-ok" />배포 중</span>}
                </div>
                {v.note && <p className="break-all text-xs text-ink">{v.note}</p>}
                <p className="break-all text-xs text-muted-foreground">{v.created_by_name ?? '알 수 없음'} · {fmtTime(v.created_at)}</p>
                <div className="flex flex-wrap gap-1">
                  <Button type="button" size="sm" variant="outline" className="h-7 px-2" aria-label={`v${v.number} 보기`} onClick={() => onView(v.number)}>
                    <Eye className="h-3.5 w-3.5" />보기
                  </Button>
                  {v.number !== deployedVersion && (
                    <span title={act.reason}>
                      <Button type="button" size="sm" className="h-7 px-2" aria-label={`v${v.number} ${act.label}`} disabled={busy || act.disabled} onClick={() => askDeploy(v.number)}>
                        <Rocket className="h-3.5 w-3.5" />{act.label}
                      </Button>
                    </span>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
