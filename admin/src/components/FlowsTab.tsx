import { Fragment, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Archive, Code2, Plus } from 'lucide-react'
import { appsFlowsApiDeployRequestsList, appsFlowsApiFlowsArchive, appsFlowsApiFlowsCreate, appsFlowsApiFlowsList } from '../generated/endpoints/flows/flows'
import { appsTenantsApiMemberMe } from '../generated/endpoints/tenant-members/tenant-members'
import type { DeployRequestOut, FlowOut } from '../generated/model'
import DeployRequestCard, { type Me } from './DeployRequestCard'
import FlowEmbedPanel from './FlowEmbedPanel'
import { HttpError } from '../mutator'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table'
import { useIsAdmin } from '../lib/useAgentRole'

const fmt = (iso: string) => new Date(iso).toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })

/** 흐름 목록 + 새 흐름 + 보관. 쓰기(만들기·보관)는 flow.edit 가 있는 Admin 만 보인다. */
export default function FlowsTab() {
  const canEdit = useIsAdmin()
  const navigate = useNavigate()
  const [flows, setFlows] = useState<FlowOut[] | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [embedId, setEmbedId] = useState<string | null>(null) // 임베드 패널이 펼쳐진 행

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

  const archive = async (id: string) => {
    setBusy(true)
    setError('')
    try {
      await appsFlowsApiFlowsArchive(id)
      setFlows((cur) => cur && cur.filter((f) => f.id !== id))
    } catch (err) {
      const body = err instanceof HttpError ? (err.body as { detail?: string } | undefined) : undefined
      setError(body?.detail ?? '흐름을 보관하지 못했습니다.')
    } finally {
      setConfirmId(null)
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
            <TableHeader><TableRow><TableHead>이름</TableHead><TableHead>배포</TableHead><TableHead>수정</TableHead><TableHead><span className="sr-only">임베드</span></TableHead>{canEdit && <TableHead><span className="sr-only">보관</span></TableHead>}</TableRow></TableHeader>
            <TableBody>
              {flows?.map((f) => (
                <Fragment key={f.id}>
                <TableRow>
                  <TableCell><Link className="font-medium text-ink underline underline-offset-4" to={`/tenant/flows/${f.id}`}>{f.name}</Link></TableCell>
                  <TableCell>
                    <Badge variant={f.deployed_version ? 'default' : 'secondary'}>{f.deployed_version ? `v${f.deployed_version} 배포 중` : '배포 전'}</Badge>
                    {f.has_pending_request && <Badge variant="secondary" className="ml-1">승인 대기</Badge>}
                  </TableCell>
                  <TableCell className="hmi-code text-muted-foreground">{fmt(f.updated_at)}</TableCell>
                  <TableCell className="whitespace-nowrap text-right">
                    {f.is_public && <Badge variant="secondary" className="mr-1">기본 흐름</Badge>}
                    {f.embed_enabled && !f.is_public && <Badge variant="default" className="mr-1">임베드 켬</Badge>}
                    <Button type="button" size="sm" variant="outline" aria-expanded={embedId === f.id} aria-label={`${f.name} 임베드`}
                      onClick={() => setEmbedId(embedId === f.id ? null : f.id)}><Code2 className="h-4 w-4" />임베드</Button>
                  </TableCell>
                  {canEdit && (
                    <TableCell className="text-right">
                      {f.is_public ? null : confirmId === f.id ? (
                        <div className="flex flex-wrap items-center justify-end gap-2">
                          <span className="text-xs text-muted-foreground">
                            {f.embed_enabled
                              ? '이 흐름에 붙여 둔 코드가 멈춥니다. 보관하면 임베드가 꺼져 키 주소가 열리지 않습니다. 목록에서 사라지고 대화 기록은 남습니다.'
                              : '보관하면 목록에서 사라집니다. 대화 기록은 남습니다.'}
                          </span>
                          <Button type="button" size="sm" variant="destructive" disabled={busy} aria-label="보관 확인" onClick={() => archive(f.id)}>보관</Button>
                          <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => setConfirmId(null)}>취소</Button>
                        </div>
                      ) : (
                        <Button type="button" size="sm" variant="outline" aria-label={`${f.name} 보관`} onClick={() => setConfirmId(f.id)}><Archive className="h-4 w-4" />보관</Button>
                      )}
                    </TableCell>
                  )}
                </TableRow>
                {embedId === f.id && (
                  <TableRow>
                    <TableCell colSpan={canEdit ? 5 : 4} className="whitespace-normal">
                      <div className="max-w-xl">
                        <FlowEmbedPanel flow={f} deployed={f.deployed_version != null} canManage={canEdit} origin={window.location.origin}
                          onChange={(next) => { setFlows((cur) => cur && cur.map((x) => (x.id === next.id ? next : next.is_public ? { ...x, is_public: false } : x))) }} />
                      </div>
                    </TableCell>
                  </TableRow>
                )}
                </Fragment>
              ))}
              {flows && flows.length === 0 && <TableRow><TableCell colSpan={canEdit ? 5 : 4} className="text-center text-muted-foreground">아직 흐름이 없습니다.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
