import { scrollLogToBottom } from '@/lib/scroll'
import { useState, useEffect, useRef, ChangeEvent } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { listEscalations, claimEscalation, sendEscalationMessage, resolveEscalation, openEscalationStream, sendTypingIndicator, getEscalationMessages, getSessionMessages, listSessions, takeoverSession } from '../api'
import type { StreamHandle } from '../api'
import type { EscalationOut, SessionListItemOut } from '../generated/model'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import Markdown from './Markdown'
import FlowFilter from './FlowFilter'

const STATUS_LABEL: Record<string, string> = { pending: '대기 중', claimed: '진행 중', resolved: '완료' }
const STATUS_VARIANT: Record<string, 'destructive' | 'default' | 'success'> = { pending: 'destructive', claimed: 'default', resolved: 'success' }
const ROLE_LABEL: Record<string, string> = { user: 'Visitor', assistant: 'AI', human_agent: '상담원' }
const ROLE_BUBBLE: Record<string, string> = { user: 'self-start bg-muted', assistant: 'self-end bg-raised border border-line-soft', human_agent: 'self-end bg-ok/15 border border-ok/50' }

type ChatMsg = { id?: string; role: string; content: string; created_at?: string }

function ChatHistory({ messages }: { messages: ChatMsg[] }) {
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => { scrollLogToBottom(bottomRef.current) }, [messages])

  if (!messages.length) return <p className="py-2 text-center text-xs text-muted-foreground">대화 내역 없음</p>

  return (
    <div data-log-scroll className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">
      {messages.map(m => (
        <div key={m.id || `${m.role}-${m.created_at}`} className={cn('flex flex-col', m.role === 'user' ? 'items-start' : 'items-end')}>
          <span className="mb-0.5 text-[10px] text-muted-foreground">{ROLE_LABEL[m.role] || m.role}</span>
          <div className={cn('max-w-[80%] break-words rounded-lg px-2.5 py-1.5 text-sm leading-relaxed', ROLE_BUBBLE[m.role] || 'self-start bg-muted')}>{m.role === 'assistant' ? <Markdown>{m.content}</Markdown> : m.content}</div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}

function EscalationCard({ esc, onUpdate, incomingMessage }: { esc: EscalationOut; onUpdate: () => void; incomingMessage: ChatMsg | null }) {
  const [msg, setMsg] = useState('')
  const [sending, setSending] = useState(false)
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const typingDebounceRef = useRef<number | undefined>(undefined)

  useEffect(() => {
    getEscalationMessages(esc.id).then(data => { if (Array.isArray(data)) setMessages(data) })
  }, [esc.id])

  useEffect(() => {
    if (incomingMessage) setMessages(prev => [...prev, incomingMessage])
  }, [incomingMessage])

  const handleClaim = async () => {
    const res = await claimEscalation(esc.id)
    if (res.status === 200 || res.ok) onUpdate()
    else if (res.status === 409) alert('이미 다른 상담원이 수락한 세션입니다.')
  }

  const handleMsgChange = (e: ChangeEvent<HTMLInputElement>) => {
    setMsg(e.target.value)
    clearTimeout(typingDebounceRef.current)
    typingDebounceRef.current = window.setTimeout(() => sendTypingIndicator(esc.id), 500)
  }

  const handleSend = async () => {
    if (!msg.trim()) return
    clearTimeout(typingDebounceRef.current)
    setSending(true)
    const content = msg.trim()
    setMsg('')
    await sendEscalationMessage(esc.id, content)
    setMessages(prev => [...prev, { role: 'human_agent', content, created_at: new Date().toISOString() }])
    setSending(false)
  }

  const handleResolve = async () => { await resolveEscalation(esc.id); onUpdate() }

  const isPending = esc.status === 'pending'
  const isClaimed = esc.status === 'claimed'

  return (
    <Card className={cn('mb-3', isPending && 'border-destructive/60 bg-destructive/5')}>
      <CardContent className="space-y-2 pt-5">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold">세션 {esc.session_id.slice(0, 8)}…</span>
          <Badge variant={STATUS_VARIANT[esc.status] || 'secondary'}>{STATUS_LABEL[esc.status]}</Badge>
        </div>
        {esc.flow_name && <p className="break-words text-xs text-muted-foreground">흐름 · <span data-testid="flow-name">{esc.flow_name}</span></p>}
        {esc.reason && <p className="break-words text-xs text-muted-foreground">{esc.reason}</p>}

        <div data-log-scroll className="max-h-60 overflow-y-auto rounded-md bg-muted/40 p-2.5">
          <ChatHistory messages={messages} />
        </div>

        {isPending && <Button size="sm" onClick={handleClaim}>수락하기</Button>}

        {isClaimed && (
          <div className="space-y-2">
            <div className="flex gap-2">
              <Input className="flex-1" value={msg} onChange={handleMsgChange}
                placeholder="방문자에게 메시지 전송..." onKeyDown={e => e.key === 'Enter' && handleSend()} disabled={sending} />
              <Button onClick={handleSend} disabled={sending || !msg.trim()}>전송</Button>
            </div>
            <Button size="sm" variant="outline" onClick={handleResolve}>AI에게 넘기기</Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// 다른 세션의 채팅 내역을 takeover 없이 읽기 전용으로 펼친다(getSessionMessages는 테넌트 스코프).
function SessionHistoryInline({ sessionId }: { sessionId: string }) {
  const [messages, setMessages] = useState<ChatMsg[] | null>(null)
  useEffect(() => {
    getSessionMessages(sessionId).then(d => setMessages(Array.isArray(d) ? (d as ChatMsg[]) : []))
  }, [sessionId])
  return (
    <div data-log-scroll className="mt-2 max-h-60 overflow-y-auto rounded-md bg-muted/40 p-2.5">
      {messages === null
        ? <p className="py-2 text-center text-xs text-muted-foreground">불러오는 중...</p>
        : <ChatHistory messages={messages} />}
    </div>
  )
}

// 세션 콘솔(ADR-0017): 상단 = 진행 중 상담(escalation 카드, claim/message/resolve 보존),
// 하단 = 다른 세션 목록(활성 먼저) + 임의 takeover. /tenant/hitl/:escalationId는 단독 딥링크.
export default function HitlTab() {
  const { escalationId } = useParams<{ escalationId?: string }>()
  const navigate = useNavigate()
  const [escalations, setEscalations] = useState<EscalationOut[]>([])
  const [sessions, setSessions] = useState<SessionListItemOut[]>([])
  const [activeIds, setActiveIds] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [flowId, setFlowId] = useState('')
  const [incomingBySession, setIncomingBySession] = useState<Record<string, ChatMsg>>({})
  const [takingOver, setTakingOver] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState<Set<string>>(new Set())
  const esRef = useRef<StreamHandle | null>(null)

  const toggleHistory = (sessionId: string) => {
    setHistoryOpen(prev => {
      const n = new Set(prev)
      if (n.has(sessionId)) n.delete(sessionId); else n.add(sessionId)
      return n
    })
  }

  const flowRef = useRef('')
  const refresh = () => {
    const f = flowRef.current || undefined
    Promise.all([listEscalations(f), listSessions(50, 0, f)]).then(([es, ss]) => {
      const escs = Array.isArray(es) ? es : []
      const sess = Array.isArray(ss) ? ss : []
      setEscalations(escs)
      setSessions(sess)
      setActiveIds(new Set(sess.filter(s => s.active).map(s => s.session_id)))
      setLoading(false)
    })
  }

  useEffect(() => {
    refresh()
    // presence 델타(ephemeral pub/sub)는 콘솔 미구독 순간(탭 전환·재연결·배포)에 놓칠 수 있다.
    // 자가치유 진실원천(active_sessions, TTL 20s)을 주기적으로 재조정해 stale "접속중"을 자동 보정한다.
    const reconcile = setInterval(refresh, 15000)
    esRef.current = openEscalationStream((event) => {
      if (event.type === 'visitor_message') {
        const msg: ChatMsg = { role: 'user', content: event.content, created_at: new Date().toISOString() }
        setIncomingBySession(prev => ({ ...prev, [event.session_id]: msg }))
      } else if (event.type === 'session_connected') {
        setActiveIds(prev => new Set(prev).add(event.session_id))
      } else if (event.type === 'session_disconnected') {
        setActiveIds(prev => { const n = new Set(prev); n.delete(event.session_id); return n })
      } else {
        refresh() // hitl_new/claimed/resolved 등 → 목록 갱신
      }
    })
    return () => { clearInterval(reconcile); esRef.current?.close() }
  }, [])

  const changeFlow = (id: string) => { setFlowId(id); flowRef.current = id; refresh() }

  if (loading) return <p className="text-sm text-muted-foreground">로딩 중...</p>

  const escSessionIds = new Set(escalations.map(e => e.session_id))
  const visibleEsc = escalationId ? escalations.filter(e => e.id === escalationId) : escalations
  // 다른 세션 = 진행 중 상담이 없는 세션. 활성(SSE) 먼저, 그다음 최근순.
  const otherSessions = escalationId ? [] : sessions
    .filter(s => !escSessionIds.has(s.session_id))
    .sort((a, b) =>
      (Number(activeIds.has(b.session_id)) - Number(activeIds.has(a.session_id))) ||
      b.last_activity.localeCompare(a.last_activity))

  const handleTakeover = async (sessionId: string) => {
    setTakingOver(sessionId)
    const r = await takeoverSession(sessionId)
    setTakingOver(null)
    if (r.status === 409) { alert('이미 다른 상담원이 상담 중인 세션입니다.'); return }
    if (r.ok) refresh()
  }

  return (
    <div className="max-w-3xl">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold">상담 콘솔</h3>
        <div className="flex gap-2">
          {escalationId && <Button size="sm" variant="ghost" onClick={() => navigate('/tenant/hitl')}>← 전체 보기</Button>}
          <FlowFilter value={flowId} onChange={changeFlow} />
          <Button size="sm" variant="outline" onClick={refresh}>새로고침</Button>
        </div>
      </div>

      {/* ── 진행 중 상담(escalation) ── */}
      {visibleEsc.length === 0 ? (
        <p className="text-sm text-muted-foreground">진행 중인 상담이 없습니다.</p>
      ) : (
        visibleEsc.map(esc => (
          <div key={esc.id}>
            {!escalationId && (
              <div className="mb-1 flex justify-end">
                <Button size="sm" variant="ghost" onClick={() => navigate(`/tenant/hitl/${esc.id}`)}>🔗 단독 보기</Button>
              </div>
            )}
            <EscalationCard esc={esc} onUpdate={refresh} incomingMessage={incomingBySession[esc.session_id] ?? null} />
          </div>
        ))
      )}

      {/* ── 다른 세션(임의 takeover) ── */}
      {!escalationId && (
        <div className="mt-8 border-t border-border pt-4">
          <h4 className="mb-2 text-sm font-semibold">다른 세션</h4>
          {otherSessions.length === 0 ? (
            <p className="text-xs text-muted-foreground">표시할 다른 세션이 없습니다.</p>
          ) : (
            <ul className="space-y-1.5">
              {otherSessions.map(s => {
                const isActive = activeIds.has(s.session_id)
                const open = historyOpen.has(s.session_id)
                return (
                  <li key={s.session_id}
                    className="rounded-md border border-border bg-card px-3 py-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="min-w-0 max-w-full break-all font-medium">{s.visitor_id}</span>
                      <span className="min-w-0 max-w-full break-words text-xs text-muted-foreground" data-testid="flow-name">{s.flow_name ?? '—'}</span>
                      <Badge variant={isActive ? 'success' : 'secondary'}>{isActive ? '접속중' : '유휴'}</Badge>
                      <span className="ml-auto text-xs text-muted-foreground">{new Date(s.last_activity).toLocaleString()}</span>
                      <Button size="sm" variant="ghost" onClick={() => toggleHistory(s.session_id)}>
                        {open ? '내역 닫기' : '내역 보기'}
                      </Button>
                      <Button size="sm" variant="outline" disabled={takingOver === s.session_id}
                        onClick={() => handleTakeover(s.session_id)}>
                        {takingOver === s.session_id ? '연결 중…' : '상담 시작'}
                      </Button>
                    </div>
                    {open && <SessionHistoryInline sessionId={s.session_id} />}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
