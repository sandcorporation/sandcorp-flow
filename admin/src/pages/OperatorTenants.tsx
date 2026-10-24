import { useState, useEffect, FormEvent } from 'react'
import { listTenants, createTenant, suspendTenant, deleteTenant, operatorAdminInvite } from '../api'
import type { TenantOut } from '../generated/model'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'

type AdminInvite = { name: string; url: string }
type CreatedKey = { name: string; key: string; inviteUrl: string }

// Operator 대시보드 콘텐츠(테넌트 생성 + 목록). 셸(헤더/로그아웃)은 DashboardLayout이 제공.
export default function OperatorTenants() {
  const [tenants, setTenants] = useState<TenantOut[]>([])
  const [newName, setNewName] = useState('')
  const [createdKey, setCreatedKey] = useState<CreatedKey | null>(null)
  const [loading, setLoading] = useState(false)
  const [ownerlessOnly, setOwnerlessOnly] = useState(false)
  const [invite, setInvite] = useState<AdminInvite | null>(null)

  const load = async () => setTenants(await listTenants())

  useEffect(() => { load() }, [])

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault()
    if (!newName.trim()) return
    setLoading(true)
    const data = await createTenant(newName.trim())
    setCreatedKey({ name: data.name, key: data.tenant_key, inviteUrl: data.admin_invite_url })
    setNewName('')
    await load()
    setLoading(false)
  }

  const handleInvite = async (t: TenantOut) => {
    const data = await operatorAdminInvite(t.id)
    setInvite({ name: t.name, url: data.url })
  }
  const ownerless = (t: TenantOut) => t.is_active && t.active_admin_count === 0
  const ownerlessCount = tenants.filter(ownerless).length
  const shown = ownerlessOnly ? tenants.filter(ownerless) : tenants

  const handleSuspend = async (id: string) => { await suspendTenant(id); await load() }
  const handleDelete = async (id: string) => {
    if (!confirm('삭제하시겠습니까?')) return
    await deleteTenant(id)
    await load()
  }

  return (
    <div className="max-w-4xl space-y-6">
      {createdKey && (
        <Card className="border-primary bg-primary/15">
          <CardContent className="space-y-1 pt-5 text-sm">
            <p><strong>{createdKey.name}</strong> 생성 완료. (1회만 표시됩니다)</p>
            <p>TENANT_KEY: <code className="rounded bg-muted px-1 py-0.5 text-xs">{createdKey.key}</code></p>
            <p>첫 Admin 초대 링크(7일·1회용 — 고객에게 직접 전달): <code className="break-all rounded bg-muted px-1 py-0.5 text-xs">{createdKey.inviteUrl}</code></p>
            <Button size="sm" variant="ghost" className="mt-1" onClick={() => setCreatedKey(null)}>✕ 닫기</Button>
          </CardContent>
        </Card>
      )}

      {invite && (
        <Card className="border-primary bg-primary/15">
          <CardContent className="space-y-1 pt-5 text-sm">
            <p><strong>{invite.name}</strong> Admin 초대 링크(7일·1회용 — 고객에게 직접 전달, 1회만 표시됩니다)</p>
            <p><code className="break-all rounded bg-muted px-1 py-0.5 text-xs">{invite.url}</code></p>
            <Button size="sm" variant="ghost" className="mt-1" onClick={() => setInvite(null)}>✕ 닫기</Button>
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        <h2 className="text-sm font-semibold">새 Tenant 추가</h2>
        <form onSubmit={handleCreate} className="flex gap-2">
          <Input className="flex-1" placeholder="고객사 이름" value={newName} onChange={e => setNewName(e.target.value)} />
          <Button type="submit" disabled={loading}>추가</Button>
        </form>
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Tenant 목록</h2>
          <Button size="sm" variant={ownerlessOnly ? 'default' : 'outline'} onClick={() => setOwnerlessOnly(v => !v)}>
            주인 없음만 보기 ({ownerlessCount})
          </Button>
        </div>
        <Table>
          <TableHeader>
            <TableRow><TableHead>이름</TableHead><TableHead>상태</TableHead><TableHead className="hidden sm:table-cell">생성일</TableHead><TableHead>작업</TableHead></TableRow>
          </TableHeader>
          <TableBody>
            {shown.map(t => (
              <TableRow key={t.id}>
                <TableCell className="max-w-[9rem] break-words whitespace-normal sm:max-w-none">{t.name}</TableCell>
                <TableCell><Badge className="whitespace-nowrap" variant={t.is_active ? 'success' : 'destructive'}>{t.is_active ? '활성' : '정지'}</Badge>
                  {ownerless(t) && <Badge variant="warning" className="mt-1 whitespace-nowrap sm:ml-1 sm:mt-0">주인 없음</Badge>}
                </TableCell>
                <TableCell className="hidden sm:table-cell">{t.created_at ? new Date(t.created_at).toLocaleDateString('ko') : '-'}</TableCell>
                <TableCell className="space-x-1 space-y-1">
                  {t.is_active && <Button size="sm" variant="outline" onClick={() => handleInvite(t)}>Admin 초대 링크 만들기</Button>}
                  {t.is_active && <Button size="sm" variant="destructive" onClick={() => handleSuspend(t.id)}>정지</Button>}
                  <Button size="sm" variant="destructive" onClick={() => handleDelete(t.id)}>삭제</Button>
                </TableCell>
              </TableRow>
            ))}
            {shown.length === 0 && (
              <TableRow><TableCell colSpan={4} className="text-center text-muted-foreground">테넌트가 없습니다</TableCell></TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
