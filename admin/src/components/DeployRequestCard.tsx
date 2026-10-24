import { useState } from 'react'
import { appsFlowsApiDeployRequestsApprove, appsFlowsApiDeployRequestsReject } from '../generated/endpoints/flows/flows'
import type { DeployRequestOut } from '../generated/model'
import { HttpError } from '../mutator'
import { approveState, isMyRequest } from '@/lib/deploy'
import { Button } from './ui/button'

export const fmtTime = (iso: string) => new Date(iso).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

export type Me = { name: string; email: string } | null

/** 승인 대기 요청 한 건. 승인·거절은 flow.approve(Admin) 만, 본인 요청은 승인할 수 없다(이유를 글로 보인다). */
export default function DeployRequestCard({ request, canApprove, me, onDecided, showFlow = false }: {
  request: DeployRequestOut
  canApprove: boolean
  me: Me
  onDecided: () => void
  showFlow?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const approve = approveState({ canApprove, isMine: !!me && isMyRequest(request.requested_by_name, me) })

  const decide = async (kind: 'approve' | 'reject') => {
    setBusy(true)
    setError('')
    try {
      await (kind === 'approve' ? appsFlowsApiDeployRequestsApprove : appsFlowsApiDeployRequestsReject)(request.id, {})
      onDecided()
    } catch (err) {
      const body = err instanceof HttpError ? (err.body as { detail?: string } | undefined) : undefined
      setError(body?.detail ?? '처리하지 못했습니다.')
      setBusy(false)
    }
  }

  return (
    <div className="space-y-1.5 border border-line-soft bg-raised p-2 text-sm" data-request-id={request.id}>
      <p className="font-medium text-ink">
        {showFlow && <span>{request.flow_name} </span>}
        {`v${request.version} 승인 대기`}
      </p>
      <p className="text-xs text-muted-foreground">{request.requested_by_name ?? '알 수 없음'} 요청 · {fmtTime(request.created_at)}</p>
      <div className="flex flex-wrap gap-1.5">
        <span title={approve.reason}>
          <Button type="button" size="sm" disabled={busy || approve.disabled} onClick={() => decide('approve')}>승인</Button>
        </span>
        <span title={canApprove ? '' : approve.reason}>
          <Button type="button" size="sm" variant="outline" disabled={busy || !canApprove} onClick={() => decide('reject')}>거절</Button>
        </span>
      </div>
      {approve.reason && <p className="text-xs text-muted-foreground">{approve.reason}</p>}
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>
  )
}
