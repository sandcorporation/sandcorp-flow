import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { currentAgentRole } from '../api'
import { appsFlowsApiDeployRequestsList, appsFlowsApiFlowsCreate, appsFlowsApiFlowsList } from '../generated/endpoints/flows/flows'
import { appsTenantsApiMemberMe } from '../generated/endpoints/tenant-members/tenant-members'
import type { DeployRequestOut, FlowOut } from '../generated/model'
import DeployRequestCard, { type Me } from './DeployRequestCard'
import { HttpError } from '../mutator'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'

const fmt = (iso: string) => new Date(iso).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })

/** 흐름 목록 + 새 흐름. 쓰기(만들기)는 flow.edit 가 있는 Admin 만 보인다. */
export default function FlowsTab() {
  const canEdit = currentAgentRole() === 'admin'
  const navigate = useNavigate()
  const [flows, setFlows] = useState<FlowOut[] | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const [requests, setRequests] = useState<DeployRequestOut[]>([])
  const [me, setMe] = useState<Me>(null)

  const loadAll = () => {
    appsFlowsApiFlowsList().then((r) => setFlows(r.data as unknown as FlowOut[])).catch(() => setError('흐름 목록을 불러오지 못했습니다.'))
    appsFlowsApiDeployRequestsList({ status: 'pending' }).then((r) => setRequests(r.data as unknown as DeployRequestOut[])).catch(() => {})
  }
  useEffect(() => {
    loadAll()
    appsTenantsApiMemberMe().then((r) => { const m = r.data as unknown as { name: string; email: string }; setMe({ name: m.name ?? '', email: m.email ?? '' }) }).catch(() => {})
  }, [])

  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    setError('')
    try {
      const r = await appsFlowsApiFlowsCreate({ name: name.trim() })
      navigate(`/tenant/flows/${(r.data as unknown as FlowOut).id}`)
    } catch (err) {
      const body = err instanceof HttpError ? (err.body as { detail?: string } | undefined) : undefined
      setError(body?.detail ?? '흐름을 만들지 못했습니다.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-cond text-lg font-semibold tracking-wide text-ink">흐름</h1>
        <p className="text-sm text-muted-foreground">방문자 대화가 지나가는 길. 노드를 이어 만들고, 시험한 뒤 배포합니다.</p>
      </div>
      {canEdit && (
        <Card>
          <CardHeader><CardTitle>새 흐름</CardTitle></CardHeader>
          <CardContent>
            <form onSubmit={create} className="flex flex-wrap items-end gap-2">
              <div className="min-w-0 flex-1 basis-56 space-y-1">
                <Label htmlFor="new-flow-name">새 흐름 이름</Label>
                <Input id="new-flow-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={255} />
              </div>
              <Button type="submit" disabled={busy || !name.trim()}><Plus className="h-4 w-4" />만들기</Button>
            </form>
          </CardContent>
        </Card>
      )}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {requests.length > 0 && (
        <section aria-label="배포 요청" className="space-y-2">
          <h2 className="hmi-label">배포 요청 · 승인 대기 {requests.length}건</h2>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {requests.map((r) => <DeployRequestCard key={r.id} request={r} showFlow canApprove={canEdit} me={me} onDecided={loadAll} />)}
          </div>
        </section>
      )}
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader><TableRow><TableHead>이름</TableHead><TableHead>배포</TableHead><TableHead>수정</TableHead></TableRow></TableHeader>
            <TableBody>
              {flows?.map((f) => (
                <TableRow key={f.id}>
                  <TableCell><Link className="font-medium text-ink underline underline-offset-4" to={`/tenant/flows/${f.id}`}>{f.name}</Link></TableCell>
                  <TableCell>
                    <Badge variant={f.deployed_version ? 'default' : 'secondary'}>{f.deployed_version ? `v${f.deployed_version} 배포 중` : '배포 전'}</Badge>
                    {f.has_pending_request && <Badge variant="secondary" className="ml-1">승인 대기</Badge>}
                  </TableCell>
                  <TableCell className="hmi-code text-muted-foreground">{fmt(f.updated_at)}</TableCell>
                </TableRow>
              ))}
              {flows && flows.length === 0 && <TableRow><TableCell colSpan={3} className="text-center text-muted-foreground">아직 흐름이 없습니다.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
