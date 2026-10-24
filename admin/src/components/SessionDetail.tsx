import { scrollLogToBottom } from '@/lib/scroll'
import { useState, useEffect, useRef, useMemo, Fragment } from 'react'
import { getSessionMessages, getSessionCheckpoint, getSessionRetrievals, type RetrievalTurn } from '../api'
import type { SessionMessageOut } from '../generated/model'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import Markdown from './Markdown'

const ROLE_LABEL: Record<string, string> = { user: 'Visitor', assistant: 'AI', human_agent: '상담원' }
const ROLE_BUBBLE: Record<string, string> = {
  user: 'self-end bg-primary text-primary-foreground',
  assistant: 'self-start bg-muted text-foreground',
  human_agent: 'self-start bg-alarm text-destructive-foreground',
}

// 한 턴의 실행 추적 — 질문과 답변 사이에 끼워 인과를 보여준다(원인→결과). 실행 노드 흐름은 항상
// 보이는 압축 경로로, 검색 근거(청크)는 접이식으로(기본 접힘).
function TurnTrace({ turn }: { turn: RetrievalTurn }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="self-stretch py-0.5">
      {turn.nodes?.length > 0 && (
        <div className="mb-0.5 flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
          <span>실행</span>
          {turn.nodes.map((n, i) => (
            <Fragment key={i}>
              {i > 0 && <span className="opacity-40">→</span>}
              <span className="rounded bg-muted px-1.5 py-0.5 font-mono">{n}</span>
            </Fragment>
          ))}
        </div>
      )}
      <button
        onClick={() => setOpen(o => !o)}
        className="text-[11px] text-muted-foreground hover:text-foreground"
      >
        {open ? '▾' : '▸'} 🔍 검색된 근거 {turn.chunk_count}개
      </button>
      {open && (
        turn.chunks.length ? (
          <ul className="mt-1 flex flex-col gap-1">
            {turn.chunks.map((c, j) => (
              <li key={j} className="break-all rounded bg-muted/40 px-2 py-1 text-[11px] leading-relaxed">{c}</li>
            ))}
          </ul>
        ) : <p className="mt-1 text-[11px] text-muted-foreground">검색 결과 없음</p>
      )}
    </div>
  )
}

export function ChatHistory({ messages, retrievals }: { messages: SessionMessageOut[]; retrievals?: RetrievalTurn[] }) {
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => { scrollLogToBottom(bottomRef.current) }, [messages])

  // 검색 근거를 유저 메시지에 순서대로 매칭(턴=유저 메시지). 내용 일치로 소비 — HITL 등 비-AI 턴은
  // 매칭이 건너뛰어져 정렬이 어긋나지 않는다.
  const turnByIdx = useMemo(() => {
    const map: Record<number, RetrievalTurn> = {}
    if (retrievals?.length) {
      let ri = 0
      messages.forEach((m, i) => {
        if (m.role === 'user' && ri < retrievals.length && retrievals[ri].user_message?.trim() === m.content?.trim()) {
          map[i] = retrievals[ri++]
        }
      })
    }
    return map
  }, [messages, retrievals])

  if (!messages.length) return <p className="py-5 text-center text-xs text-muted-foreground">대화 내역 없음</p>

  return (
    <div className="flex flex-col gap-2">
      {messages.map((m, i) => (
        <Fragment key={m.id || i}>
          <div className={cn('flex flex-col', m.role === 'user' ? 'items-end' : 'items-start')}>
            <span className="mb-0.5 text-[10px] text-muted-foreground">{ROLE_LABEL[m.role] || m.role}</span>
            <div className={cn('max-w-[75%] break-words rounded-lg px-3 py-2 text-sm leading-relaxed', m.role === 'assistant' ? '' : 'whitespace-pre-wrap', ROLE_BUBBLE[m.role] || 'self-start bg-muted')}>
              {m.role === 'assistant' ? <Markdown>{m.content}</Markdown> : m.content}
            </div>
            {m.created_at && <span className="mt-0.5 text-[10px] text-muted-foreground">{new Date(m.created_at).toLocaleString()}</span>}
          </div>
          {turnByIdx[i] && <TurnTrace turn={turnByIdx[i]} />}
        </Fragment>
      ))}
      <div ref={bottomRef} />
    </div>
  )
}

function CheckpointView({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<unknown>(undefined)
  useEffect(() => { getSessionCheckpoint(sessionId).then(setData) }, [sessionId])

  if (data === undefined) return <p className="text-sm text-muted-foreground">불러오는 중...</p>
  if (data === null) return <p className="text-sm text-muted-foreground">이 세션은 AI 호출 내역이 없습니다.</p>

  return (
    <pre className="max-h-[400px] overflow-y-auto whitespace-pre-wrap break-all rounded-md border border-border bg-muted/40 p-3 text-[11px] leading-relaxed">
      {JSON.stringify(data, null, 2)}
    </pre>
  )
}

export default function SessionDetail({ sessionId, onBack }: { sessionId: string; onBack: () => void }) {
  const [subTab, setSubTab] = useState('history')
  const [messages, setMessages] = useState<SessionMessageOut[]>([])
  const [retrievals, setRetrievals] = useState<RetrievalTurn[]>([])

  useEffect(() => {
    getSessionMessages(sessionId).then(data => setMessages(Array.isArray(data) ? data : []))
    getSessionRetrievals(sessionId).then(data => setRetrievals(data ?? []))
  }, [sessionId])

  return (
    <div>
      <Button size="sm" variant="outline" className="mb-4" onClick={onBack}>← 뒤로</Button>
      <div className="mb-3 text-xs text-muted-foreground">세션 <strong>{sessionId.slice(0, 8)}…</strong></div>

      <div className="mb-4 flex gap-2 border-b border-border pb-2">
        {(['history', 'checkpoint'] as const).map(t => (
          <Button key={t} size="sm" variant={subTab === t ? 'default' : 'outline'} onClick={() => setSubTab(t)}>
            {{ history: '대화 내역', checkpoint: 'Checkpoint' }[t]}
          </Button>
        ))}
      </div>

      <div className="max-h-[480px] overflow-y-auto">
        {subTab === 'history' && <ChatHistory messages={messages} retrievals={retrievals} />}
        {subTab === 'checkpoint' && <CheckpointView sessionId={sessionId} />}
      </div>
    </div>
  )
}
