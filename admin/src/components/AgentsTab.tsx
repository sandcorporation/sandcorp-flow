import { useState, useEffect, FormEvent } from 'react'
import { listMembers, listInvites, createInvite, revokeInvite, changeMemberRole, deactivateMember, memberMe } from '../api'
import { HttpError } from '../mutator'
import type { MemberOut } from '../generated/model'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { useIsAdmin } from '../lib/useAgentRole'

const ROLE_LABEL: Record<string, string> = { admin: '관리자', member: '팀원' }

type Invite = { id: string; role: string; expires_at: string; created_at: string }
type CreatedInvite = Invite & { url: string }

function formatExpiry(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// 이름 → 이메일 → sub 끝 6자리 순으로 보여 준다(이름·이메일은 sandcorp-auth에서 받은 캐시다).
function memberLabel(m: MemberOut): string {
  return m.name || m.email || `계정 …${m.sub_tail}`
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

// 팀원(멤버십) 관리 + 초대 링크(#40, #56). 비밀번호 계정은 없다 — 초대 링크로 sandcorp 계정 로그인하면 팀원이 된다.
export default function AgentsTab() {
  const isAdmin = useIsAdmin()
  const [members, setMembers] = useState<MemberOut[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [myId, setMyId] = useState<string | null>(null)
  const [newRole, setNewRole] = useState<'admin' | 'member'>('member')
  const [created, setCreated] = useState<CreatedInvite | null>(null)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadMembers = async () => setMembers(await listMembers())
  const loadInvites = async () => { if (isAdmin) setInvites((await listInvites()) as Invite[]) }

  useEffect(() => {
    loadMembers().catch(() => setError('팀원 목록을 불러오지 못했습니다.'))
    memberMe().then((me) => setMyId(me.id)).catch(() => {})
  }, [])
  // 역할을 서버에서 읽은 뒤(또는 바뀐 뒤) Admin 이면 초대 목록을 불러온다.
  useEffect(() => { loadInvites().catch(() => {}) }, [isAdmin])

  const handleInvite = async (e: FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    setCopied(false)
    try {
      const data = await createInvite(newRole)
      setCreated({ id: data.id, url: data.url, role: data.role, expires_at: data.expires_at, created_at: data.created_at })
      await loadInvites()
    } catch {
      setError('초대 링크를 만들지 못했습니다.')
    }
    setLoading(false)
  }

  const handleCopy = async () => {
    if (created && (await copyText(created.url))) setCopied(true)
  }

  const handleRevoke = async (id: string) => {
    setError('')
    try {
      await revokeInvite(id)
      if (created?.id === id) setCreated(null)
      await loadInvites()
    } catch {
      setError('초대 링크를 취소하지 못했습니다.')
    }
  }

  const handleRole = async (m: MemberOut, role: 'admin' | 'member') => {
    setError('')
    try {
      await changeMemberRole(m.id, role)
      await loadMembers()
    } catch (e) {
      setError(e instanceof HttpError && e.status === 409 ? '마지막 관리자는 팀원으로 바꿀 수 없습니다. 다른 관리자를 먼저 지정하세요.' : '역할을 바꾸지 못했습니다.')
    }
  }

  const handleRemove = async (m: MemberOut) => {
    if (!confirm(`${memberLabel(m)}님을 이 조직에서 내보내시겠습니까?`)) return
    setError('')
    try {
      await deactivateMember(m.id)
      await loadMembers()
    } catch (e) {
      setError(e instanceof HttpError && e.status === 409 ? '마지막 관리자는 내보낼 수 없습니다. 다른 관리자를 먼저 지정하세요.' : '내보내지 못했습니다.')
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      {isAdmin && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">팀원 초대</h2>
          <form onSubmit={handleInvite} className="flex flex-wrap items-center gap-2">
            <select className="h-9 rounded-md border border-input bg-background px-2 text-sm" value={newRole}
              onChange={e => setNewRole(e.target.value as 'admin' | 'member')} aria-label="역할">
              <option value="member">팀원</option>
              <option value="admin">관리자</option>
            </select>
            <Button type="submit" disabled={loading}>초대 링크 만들기</Button>
            <span className="text-xs text-muted-foreground">링크는 7일 동안, 한 사람만 쓸 수 있습니다.</span>
          </form>

          {created && (
            <Card className="border-primary bg-primary/15 border-l-4">
              <CardContent className="space-y-2 pt-5 text-sm">
                <p>
                  {ROLE_LABEL[created.role] ?? created.role} 초대 링크 · {formatExpiry(created.expires_at)}까지.
                  이 링크는 지금 한 번만 보입니다 — 직접 전달하세요.
                </p>
                <div className="flex gap-2">
                  <Input readOnly aria-label="초대 링크" value={created.url} onFocus={e => e.target.select()} />
                  <Button type="button" variant="outline" onClick={handleCopy}>{copied ? '복사됨' : '복사'}</Button>
                  <Button type="button" variant="ghost" onClick={() => setCreated(null)} aria-label="닫기">✕</Button>
                </div>
              </CardContent>
            </Card>
          )}

          <div className="space-y-1">
            <h3 className="text-xs font-semibold text-muted-foreground">대기 중인 초대</h3>
            {invites.length === 0 ? (
              <p className="text-xs text-muted-foreground">대기 중인 초대가 없습니다.</p>
            ) : (
              <ul className="divide-y divide-border rounded-md border border-border text-sm">
                {invites.map(i => (
                  <li key={i.id} className="flex items-center justify-between gap-2 px-3 py-2">
                    <span className="min-w-0">
                      <Badge variant={i.role === 'admin' ? 'default' : 'secondary'}>{ROLE_LABEL[i.role] ?? i.role}</Badge>
                      <span className="ml-2 text-xs text-muted-foreground">{formatExpiry(i.expires_at)}까지</span>
                    </span>
                    <Button size="sm" variant="outline" onClick={() => handleRevoke(i.id)}>초대 취소</Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold">팀원 목록</h2>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {!isAdmin && <p className="text-xs text-muted-foreground">팀원 관리는 관리자만 할 수 있습니다.</p>}
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>팀원</TableHead><TableHead>역할</TableHead><TableHead>상태</TableHead>{isAdmin && <TableHead>작업</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {members.map(m => (
              <TableRow key={m.id}>
                <TableCell>
                  <div className="font-medium">{memberLabel(m)}{m.id === myId && <span className="ml-1 text-xs font-normal text-muted-foreground">(나)</span>}</div>
                  {m.name && m.email && <div className="text-xs text-muted-foreground">{m.email}</div>}
                </TableCell>
                <TableCell><Badge className="whitespace-nowrap" variant={m.role === 'admin' ? 'default' : 'secondary'}>{ROLE_LABEL[m.role] ?? m.role}</Badge></TableCell>
                <TableCell><Badge className="whitespace-nowrap" variant={m.is_active ? 'success' : 'secondary'}>{m.is_active ? '활성' : '내보냄'}</Badge></TableCell>
                {isAdmin && (
                  <TableCell>
                    {m.is_active && (
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" variant="outline" onClick={() => handleRole(m, m.role === 'admin' ? 'member' : 'admin')}>
                          {m.role === 'admin' ? '팀원으로' : '관리자로'}
                        </Button>
                        <Button size="sm" variant="destructive" onClick={() => handleRemove(m)}>내보내기</Button>
                      </div>
                    )}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  )
}
