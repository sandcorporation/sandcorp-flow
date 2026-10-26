import { useCallback, useEffect, useRef, useState } from 'react'
import { appsMemoryApiGetSessionDecisionTraces } from '../generated/endpoints/sessions/sessions'
import { appsTenantsApiGetTrial } from '../generated/endpoints/tenant/tenant'
import { HttpError } from '../mutator'
import type { FlowError, FlowGraph } from '@/lib/flowGraph'
import { useFlowEditorBackend } from '@/lib/flowEditorBackend'
import { tryRefusal, type TryRefusal } from '@/lib/tryRefusal'
import {
  entryLabel, formatCost, formatTokens, notRunMessage, restoredTurns, sessionTotals, turnTotals,
  type SseEvent, type TestTurn, type Totals, type TraceEntry,
} from '@/lib/testTrace'
import { Button } from './ui/button'
import { Textarea } from './ui/textarea'
import { notifyTrialChanged } from './TrialBanner'

const blank = (n: number, message: string): TestTurn => ({ n, message, reply: '', entries: [], ran: false, handoff: false, note: '', errors: [], failure: '', pending: true })

// 새로고침해도 판단 기록이 남도록, 초안(흐름)마다 마지막 시험 세션 id 를 이 브라우저에 기억한다(없어도 화면은 돈다).
const sessionKey = (flowId: string) => `flow-test-session:${flowId}`
const remember = (flowId: string, id: string | null) => {
  try { if (id) window.localStorage.setItem(sessionKey(flowId), id); else window.localStorage.removeItem(sessionKey(flowId)) } catch { /* 저장소 불가 */ }
}

/** 시험 대화 상태: 같은 session_id 로 턴을 이어 가고(재개 지점), 「새 대화」로 비운다. */
export function useTestSession(flowId: string, beforeSend: () => Promise<void>) {
  const backend = useFlowEditorBackend()
  const [refusal, setRefusal] = useState<TryRefusal | null>(null) // 체험 한도 사유(done.code) — 마지막 턴 기준
  const [turns, setTurns] = useState<TestTurn[]>([])
  const [busy, setBusy] = useState(false)
  const sessionRef = useRef<string | null>(null)
  const nextN = useRef(1)

  useEffect(() => {
    if (!backend.restoreTraces) return // 체험엔 저장된 판단 기록 API 가 없다 — SSE trace 로만 그린다
    let id: string | null = null
    try { id = window.localStorage.getItem(sessionKey(flowId)) } catch { /* 저장소 불가 */ }
    if (!id) return
    let alive = true
    appsMemoryApiGetSessionDecisionTraces(id)
      .then((r) => {
        if (!alive) return
        const restored = restoredTurns((r.data as { turns: { turn: number; entries: unknown[] }[] }).turns)
        if (restored.length === 0 || sessionRef.current) return
        sessionRef.current = id
        nextN.current = restored[restored.length - 1].n + 1
        setTurns(restored)
      })
      .catch(() => { if (alive) remember(flowId, null) }) // 세션이 없어졌다 — 기억을 지운다
    return () => { alive = false }
  }, [flowId, backend])

  const patchLast = (f: (t: TestTurn) => TestTurn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? f(t) : t)))

  /** 턴을 보낸다. 보내지 못했으면(저장·세션 생성 거절 포함) false — 입력창이 쓴 글을 되살린다. */
  const send = useCallback(async (message: string): Promise<boolean> => {
    let sent = true
    setBusy(true)
    const n = nextN.current++
    setTurns((ts) => [...ts, blank(n, message)])
    let done: { ran: boolean; note: string; errors: FlowError[]; code: string | null } | null = null
    const onEvent = ({ event, data }: SseEvent) => {
      if (event === 'trace') patchLast((t) => ({ ...t, entries: [...t.entries, data as TraceEntry] }))
      else if (event === 'reply') patchLast((t) => ({ ...t, reply: String(data.text ?? '') }))
      else if (event === 'done') {
        if (data.session_id) { sessionRef.current = String(data.session_id); if (backend.restoreTraces) remember(flowId, sessionRef.current) }
        done = { ran: !!data.ran, note: String(data.note ?? ''), errors: (data.errors ?? []) as FlowError[], code: data.code ? String(data.code) : null }
        patchLast((t) => ({ ...t, ran: done!.ran, handoff: !!data.handoff, note: done!.note, errors: done!.errors }))
      }
    }
    try {
      await beforeSend() // 편집 중인 초안 저장이 끝난 뒤에 시험한다
      setRefusal(null)
      await backend.streamTest(flowId, message, sessionRef.current, onEvent)
      if (done) notifyTrialChanged() // 턴이 끝났으니 상단 체험 배너의 남은 턴을 다시 읽게 한다(#103)
      if (done && !(done as { ran: boolean }).ran) {
        const d = done as { note: string; errors: FlowError[]; code: string | null }
        const why = tryRefusal(d.code, d.note)
        if (why) setRefusal(why)
        let trial: string | null = null
        if (!why && backend.askTrial && d.errors.length === 0) trial = await appsTenantsApiGetTrial().then((r) => (r.data as { state?: string }).state ?? null).catch(() => null)
        patchLast((t) => ({ ...t, failure: why ? '' : notRunMessage(d.errors, d.note, trial) }))
      }
    } catch (err) {
      sent = false
      const status = err instanceof HttpError ? err.status : 0
      const detail = err instanceof HttpError ? (err.body as { detail?: string } | undefined)?.detail : undefined
      const failure = status === 429 && detail ? `저장하지 못했습니다: ${detail} 쓴 글은 입력창에 남겨 뒀어요.`
        : status === 403 ? '시험 대화를 돌릴 권한이 없습니다. 관리자에게 요청하세요.'
        : status === 404 ? '이 흐름을 찾지 못했습니다.' : '시험 대화를 보내지 못했습니다. 잠시 뒤 다시 시도하세요.'
      patchLast((t) => ({ ...t, failure }))
    } finally {
      patchLast((t) => ({ ...t, pending: false }))
      setBusy(false)
    }
    return sent
  }, [flowId, beforeSend, backend])

  const reset = useCallback(() => { sessionRef.current = null; if (backend.restoreTraces) remember(flowId, null); nextN.current = 1; setTurns([]); setRefusal(null) }, [flowId, backend])
  return { turns, busy, send, reset, refusal }
}

export function TestChat({ turns, busy, send, reset, readOnly, refusal }: {
  turns: TestTurn[]; busy: boolean; send: (m: string) => void | Promise<boolean>; reset: () => void; readOnly: boolean; refusal?: TryRefusal | null
}) {
  const closed = refusal?.closed === true // 세션 턴을 다 쓴 뒤엔 더 보내지 않는다
  const [text, setText] = useState('')
  const submit = () => {
    const m = text.trim()
    if (!m || busy || readOnly || closed) return
    setText('')
    void Promise.resolve(send(m)).then((sent) => { if (sent === false) setText((cur) => cur || m) }) // 못 보냈으면 쓴 글을 돌려 둔다
  }
  return (
    <section className="space-y-2" aria-label="시험 대화">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">저장된 초안으로 돕니다. 배포하지 않아도 되고 실제 넘김·알림은 없습니다. 체험 턴을 1 씁니다.</p>
        <Button type="button" variant="outline" size="sm" onClick={reset} disabled={busy || turns.length === 0}>새 대화</Button>
      </div>
      {readOnly && <p className="border-l-2 border-line-soft pl-2 text-sm text-muted-foreground">관리자만 시험 대화를 돌릴 수 있습니다. 판단 기록은 볼 수 있습니다.</p>}
      {refusal && (
        <p role="alert" data-code={refusal.code} className="space-y-0.5 border-l-2 border-alarm pl-2 text-sm">
          <strong className="block text-ink">{refusal.title}</strong>
          <span className="block text-muted-foreground">{refusal.text}</span>
        </p>
      )}
      <ol className="space-y-2" aria-live="polite">
        {turns.map((t) => (
          <li key={t.n} className="space-y-1.5 text-sm">
            <p className="ml-6 whitespace-pre-wrap border border-line-soft bg-raised px-2 py-1">{t.message}</p>
            {t.reply && <p data-reply className="mr-6 whitespace-pre-wrap border border-ink bg-background px-2 py-1">{t.reply}</p>}
            {t.pending && !t.reply && <p className="text-xs text-muted-foreground" role="status">돌리는 중…</p>}
            {t.handoff && <p className="text-xs text-muted-foreground">{t.note}</p>}
            {t.errors.map((e, i) => <p key={i} className="hmi-code border-l-2 border-alarm pl-2 text-muted-foreground">{e.code}: {e.message}</p>)}
            {t.failure && <p role="alert" className="border-l-2 border-alarm pl-2 text-sm">{t.failure}</p>}
          </li>
        ))}
      </ol>
      <div className="flex items-end gap-2">
        <Textarea
          aria-label="시험 메시지" rows={2} value={text} disabled={readOnly || closed} placeholder="방문자처럼 말해 보세요"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }}
        />
        <Button type="button" onClick={submit} disabled={readOnly || closed || busy || !text.trim()}>보내기</Button>
      </div>
    </section>
  )
}

const totalsLine = (t: Totals) => `${formatTokens(t.input, t.output)} 토큰 · ${formatCost(t.cost, t.input + t.output > 0)} · ${t.duration}ms`

const short = (v: unknown) => (typeof v === 'string' ? v : JSON.stringify(v)).slice(0, 160)

/** 판단 기록: 턴별 노드 항목. 항목을 누르면 캔버스에서 그 노드를, 턴 이름을 누르면 지나간 경로를 강조한다. */
export function DecisionLog({ turns, graph, onFocus }: {
  turns: TestTurn[]; graph: FlowGraph; onFocus: (turn: TestTurn, nodeId: string | null) => void
}) {
  const ran = turns.filter((t) => t.entries.length > 0)
  return (
    <section className="space-y-3 font-mono text-[0.78rem]" aria-label="판단 기록">
      {ran.length === 0 && <p className="font-sans text-sm text-muted-foreground">시험 대화를 돌리면 노드마다 무엇을 보고 왜 갈렸는지, 토큰·비용·시간이 여기 쌓입니다.</p>}
      {ran.map((t) => {
        const total = turnTotals(t.entries)
        return (
          <div key={t.n} className="border border-line-soft">
            <button type="button" className="flex w-full items-center justify-between gap-2 bg-head px-2 py-1 text-left text-head-foreground" onClick={() => onFocus(t, null)} aria-label={`턴 ${t.n} 경로 강조`}>
              <span className="font-semibold">턴 {t.n}</span>
              <span className="text-[0.7rem] opacity-90">{`합계 ${totalsLine(total)}`}</span>
            </button>
            <ul className="divide-y divide-line-soft">
              {t.entries.map((e, i) => {
                const hasUsage = e.input_tokens + e.output_tokens > 0
                const rec = e.record as { reason?: unknown; input?: unknown; output?: unknown }
                const internal = !graph.nodes.some((n) => n.id === e.node_id)
                return (
                  <li key={i}>
                    <button type="button" disabled={internal} className="block w-full px-2 py-1.5 text-left hover:bg-raised disabled:cursor-default disabled:hover:bg-transparent" onClick={() => onFocus(t, e.node_id)}>
                      <span className="flex flex-wrap items-baseline gap-x-3">
                        <span className="font-semibold text-ink">{entryLabel(e, graph)}</span>
                        <span className="text-muted-foreground">{e.handle}</span>
                        <span className="ml-auto text-muted-foreground">{hasUsage && <span>{formatTokens(e.input_tokens, e.output_tokens)}</span>}</span>
                        {hasUsage && <span>{formatCost(e.cost_usd, hasUsage)}</span>}
                        <span className="text-muted-foreground">{e.duration_ms}ms</span>
                      </span>
                      {rec.reason !== undefined && <span className="mt-0.5 block break-words text-ink">{short(rec.reason)}</span>}
                      {rec.input !== undefined && <span className="block break-words text-muted-foreground">입력 {short(rec.input)}</span>}
                      {rec.output !== undefined && <span className="block break-words text-muted-foreground">출력 {short(rec.output)}</span>}
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        )
      })}
      {ran.length > 0 && <p className="border-t border-ink pt-1.5 font-semibold">{`세션 합계 ${totalsLine(sessionTotals(ran))}`}</p>}
    </section>
  )
}
