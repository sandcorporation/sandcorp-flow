import { useState, useEffect, FormEvent } from 'react'
import { listMembers, createInvite, changeMemberRole, deactivateMember, currentAgentRole } from '../api'
import { HttpError } from '../mutator'
import type { MemberOut } from '../generated/model'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'

const ROLE_LABEL: Record<string, string> = { admin: 'Admin', member: 'Member' }

// 팀원(멤버십) 관리 + 초대 링크(#40). 비밀번호 계정은 없다 — 초대 링크로 sandcorp-auth 로그인하면 멤버가 된다.
// 최소 화면이다(대기 중 초대 목록·폐기 화면은 #56).
export default function AgentsTab() {
  const isAdmin = currentAgentRole() === 'admin'
  const [members, setMembers] = useState<MemberOut[]>([])
  const [newRole, setNewRole] = useState<'admin' | 'member'>('member')
  const [invite, setInvite] = useState<{ url: string; role: string; expires_at: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = async () => setMembers(await listMembers())

  useEffect(() => { load() }, [])

  const handleInvite = async (e: FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const data = await createInvite(newRole)
      setInvite({ url: data.url, role: data.role, expires_at: data.expires_at })
    } catch {
      setError('초대 링크를 만들지 못했습니다.')
    }
    setLoading(false)
  }

  const handleRole = async (m: MemberOut, role: 'admin' | 'member') => {
    setError('')
    try {
      await changeMemberRole(m.id, role)
      await load()
    } catch (e) {
      setError(e instanceof HttpError && e.status === 409 ? '마지막 Admin은 강등할 수 없습니다.' : '역할 변경에 실패했습니다.')
    }
  }

  const handleDeactivate = async (id: string) => {
    if (!confirm('비활성화하시겠습니까?')) return
    setError('')
    try {
      await deactivateMember(id)
      await load()
    } catch (e) {
      setError(e instanceof HttpError && e.status === 409 ? '마지막 Admin은 비활성화할 수 없습니다.' : '비활성화에 실패했습니다.')
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      {invite && (
        <Card className="border-amber-300 bg-amber-50 dark:bg-amber-950/30">
          <CardContent className="flex items-center gap-2 pt-5 text-sm">
            <span className="min-w-0 flex-1">
              {ROLE_LABEL[invite.role]} 초대 링크 (7일·1회용, 이 화면에서 한 번만 표시 — 직접 전달하세요):
              <Input readOnly aria-label="초대 링크" className="mt-1" value={invite.url} onFocus={e => e.target.select()} />
            </span>
            <Button size="sm" variant="ghost" onClick={() => setInvite(null)}>✕</Button>
          </CardContent>
        </Card>
      )}

      {isAdmin && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold">팀원 초대</h2>
          <form onSubmit={handleInvite} className="flex gap-2">
            <select className="rounded-md border border-input bg-background px-2 text-sm" value={newRole}
              onChange={e => setNewRole(e.target.value as 'admin' | 'member')} aria-label="역할">
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
            <Button type="submit" disabled={loading}>초대 링크 만들기</Button>
          </form>
        </div>
      )}

      <div className="space-y-2">
        <h2 className="text-sm font-semibold">팀원 목록</h2>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!isAdmin && <p className="text-xs text-muted-foreground">팀원 관리는 Admin만 할 수 있습니다.</p>}
        <Table>
          <TableHeader>
            <TableRow><TableHead>이름</TableHead><TableHead>역할</TableHead><TableHead>상태</TableHead>{isAdmin && <TableHead>작업</TableHead>}</TableRow>
          </TableHeader>
          <TableBody>
            {members.map(a => (
              <TableRow key={a.id}>
                <TableCell>{a.name || a.email || '(이름 없음)'}</TableCell>
                <TableCell><Badge variant={a.role === 'admin' ? 'default' : 'secondary'}>{ROLE_LABEL[a.role] || a.role}</Badge></TableCell>
                <TableCell><Badge variant={a.is_active ? 'success' : 'secondary'}>{a.is_active ? '활성' : '비활성'}</Badge></TableCell>
                {isAdmin && (
                  <TableCell className="flex gap-2">
                    {a.is_active && (
                      <Button size="sm" variant="outline" onClick={() => handleRole(a, a.role === 'admin' ? 'member' : 'admin')}>
                        {a.role === 'admin' ? 'Member로' : 'Admin으로'}
                      </Button>
                    )}
                    {a.is_active && <Button size="sm" variant="destructive" onClick={() => handleDeactivate(a.id)}>비활성화</Button>}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

    </div>
  )
}
