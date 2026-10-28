import { useState, useEffect, ReactNode } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { listVisitors, listVisitorSessions, listMemories, updateMemory, deleteMemory } from '../api'
import type { VisitorOut, VisitorSessionOut, MemoryOut } from '../generated/model'
import FlowFilter from './FlowFilter'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { cn } from '@/lib/utils'

function VisitorList({ selectedId, onSelect }: { selectedId: string | null; onSelect: (id: string) => void }) {
  const [search, setSearch] = useState('')
  const [visitors, setVisitors] = useState<VisitorOut[]>([])

  const load = async (q?: string) => {
    const data = await listVisitors(q || undefined)
    setVisitors(Array.isArray(data) ? data : [])
  }

  useEffect(() => { load('') }, [])

  return (
    <div className="w-56 flex-shrink-0 border-r border-border pr-4">
      <div className="mb-3 flex gap-1.5">
        <Input className="h-8 flex-1 text-xs" placeholder="visitor_id 검색" value={search}
          onChange={e => setSearch(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && load(e.currentTarget.value)} />
        <Button size="sm" variant="outline" onClick={() => load(search)}>검색</Button>
      </div>
      {visitors.length === 0 && <p className="text-xs text-muted-foreground">방문자 없음</p>}
      {visitors.map(v => (
        <button key={v.visitor_id} onClick={() => onSelect(v.visitor_id)}
          className={cn(
            'mb-1 block w-full break-all rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent',
            selectedId === v.visitor_id && 'bg-accent font-medium',
          )}>
          {v.visitor_id}
        </button>
      ))}
    </div>
  )
}

function SessionList({ visitorId, onSelectSession }: { visitorId: string; onSelectSession: (id: string) => void }) {
  const [sessions, setSessions] = useState<VisitorSessionOut[]>([])
  const [flowId, setFlowId] = useState('')

  useEffect(() => {
    listVisitorSessions(visitorId, flowId || undefined).then(data => setSessions(Array.isArray(data) ? data : []))
  }, [visitorId, flowId])

  const filter = <div className="mb-1"><FlowFilter value={flowId} onChange={setFlowId} /></div>
  if (sessions.length === 0) return <div>{filter}<p className="text-xs text-muted-foreground">세션 없음</p></div>

  return (
    <div className="flex flex-col gap-1.5">
      {filter}
      {sessions.map(sess => (
        <button key={sess.session_id} onClick={() => onSelectSession(sess.session_id)}
          className="rounded-md border border-border bg-card px-3 py-2.5 text-left transition-colors hover:bg-accent">
          <div className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            {sess.session_id.slice(0, 8)}…
            {sess.is_hitl && <Badge variant="secondary" className="bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300">HITL</Badge>}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            <span data-testid="flow-name">{sess.flow_name ?? '—'}</span> · {new Date(sess.created_at).toLocaleString()}
          </div>
        </button>
      ))}
    </div>
  )
}

function MemoryEditor({ visitorId }: { visitorId: string }) {
  const [memories, setMemories] = useState<MemoryOut[]>([])
  const [editing, setEditing] = useState<MemoryOut | null>(null)

  useEffect(() => {
    listMemories(visitorId).then(data => setMemories(Array.isArray(data) ? data : []))
  }, [visitorId])

  const handleDelete = async (memId: string) => {
    await deleteMemory(visitorId, memId)
    setMemories(m => m.filter(x => x.id !== memId))
  }

  const handleUpdate = async (mem: MemoryOut) => {
    const updated = await updateMemory(visitorId, mem.id, { key: mem.key, value: mem.value })
    setMemories(m => m.map(x => x.id === mem.id ? updated : x))
    setEditing(null)
  }

  if (memories.length === 0) return <p className="text-xs text-muted-foreground">Memory 없음</p>

  return (
    <Table>
      <TableHeader>
        <TableRow><TableHead>Key</TableHead><TableHead>Value</TableHead><TableHead>작업</TableHead></TableRow>
      </TableHeader>
      <TableBody>
        {memories.map(m => (
          <TableRow key={m.id}>
            <TableCell>{editing?.id === m.id
              ? <Input className="h-8" value={editing.key} onChange={e => setEditing(x => x ? { ...x, key: e.target.value } : x)} />
              : m.key}</TableCell>
            <TableCell>{editing?.id === m.id
              ? <Input className="h-8" value={editing.value} onChange={e => setEditing(x => x ? { ...x, value: e.target.value } : x)} />
              : m.value}</TableCell>
            <TableCell>
              {editing?.id === m.id ? (
                <>
                  <Button size="sm" onClick={() => handleUpdate(editing!)}>저장</Button>
                  <Button size="sm" variant="ghost" className="ml-1" onClick={() => setEditing(null)}>취소</Button>
                </>
              ) : (
                <>
                  <Button size="sm" variant="outline" onClick={() => setEditing({ ...m })}>수정</Button>
                  <Button size="sm" variant="destructive" className="ml-1" onClick={() => handleDelete(m.id)}>삭제</Button>
                </>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-6">
      <h5 className="mb-2 text-sm font-semibold text-foreground">{title}</h5>
      {children}
    </div>
  )
}

// 자원 라우트(ADR-0017): /tenant/visitors(:visitorId?) — visitor 선택이 URL을 바꾼다.
// 세션 선택은 /tenant/sessions/:sessionId로 이동(SessionDetailPage).
export default function VisitorsTab() {
  const { visitorId } = useParams<{ visitorId?: string }>()
  const navigate = useNavigate()

  return (
    <div className="flex min-h-[500px] gap-6">
      <VisitorList selectedId={visitorId ?? null} onSelect={id => navigate(`/tenant/visitors/${id}`)} />
      <div className="min-w-0 flex-1">
        {!visitorId ? (
          <p className="pt-10 text-center text-sm text-muted-foreground">왼쪽에서 방문자를 선택하세요</p>
        ) : (
          <div>
            <h4 className="mb-3 break-all text-sm font-semibold">{visitorId}</h4>
            <Section title="세션 목록">
              <SessionList visitorId={visitorId} onSelectSession={sid => navigate(`/tenant/sessions/${sid}`)} />
            </Section>
            <Section title="Memory">
              <MemoryEditor visitorId={visitorId} />
            </Section>
          </div>
        )}
      </div>
    </div>
  )
}
