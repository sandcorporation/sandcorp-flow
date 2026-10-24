import { useCallback, useRef, useState } from 'react'
import { appsTenantsApiGetTrial } from '../generated/endpoints/tenant/tenant'
import { HttpError } from '../mutator'
import type { FlowError, FlowGraph } from '@/lib/flowGraph'
import { streamTestTurn } from '@/lib/testStream'
import {
  entryLabel, formatCost, formatTokens, notRunMessage, sessionTotals, turnTotals,
  type SseEvent, type TestTurn, type Totals, type TraceEntry,
} from '@/lib/testTrace'
import { Button } from './ui/button'
import { Textarea } from './ui/textarea'

const blank = (n: number, message: string): TestTurn => ({ n, message, reply: '', entries: [], ran: false, handoff: false, note: '', errors: [], failure: '', pending: true })

/** 시험 대화 상태: 같은 session_id 로 턴을 이어 가고(재개 지점), 「새 대화」로 비운다. */
export function useTestSession(flowId: string, beforeSend: () => Promise<void>) {
  const [turns, setTurns] = useState<TestTurn[]>([])
  const [busy, setBusy] = useState(false)
  const sessionRef = useRef<string | null>(null)
  const nextN = useRef(1)

  const patchLast = (f: (t: TestTurn) => TestTurn) => setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? f(t) : t)))

  const send = useCallback(async (message: string) => {
    setBusy(true)
    const n = nextN.current++
    setTurns((ts) => [...ts, blank(n, message)])
    let done: { ran: boolean; note: string; errors: FlowError[] } | null = null
    const onEvent = ({ event, data }: SseEvent) => {
      if (event === 'trace') patchLast((t) => ({ ...t, entries: [...t.entries, data as TraceEntry] }))
      else if (event === 'reply') patchLast((t) => ({ ...t, reply: String(data.text ?? '') }))
      else if (event === 'done') {
        if (data.session_id) sessionRef.current = String(data.session_id)
        done = { ran: !!data.ran, note: String(data.note ?? ''), errors: (data.errors ?? []) as FlowError[] }
        patchLast((t) => ({ ...t, ran: done!.ran, handoff: !!data.handoff, note: done!.note, errors: done!.errors }))
      }
    }
    try {
      await beforeSend() // 편집 중인 초안 저장이 끝난 뒤에 시험한다
      await streamTestTurn(flowId, message, sessionRef.current, onEvent)
      if (done && !(done as { ran: boolean }).ran) {
        const d = done as { note: string; errors: FlowError[] }
        let trial: string | null = null
        if (d.errors.length === 0) trial = await appsTenantsApiGetTrial().then((r) => (r.data as { state?: string }).state ?? null).catch(() => null)
        patchLast((t) => ({ ...t, failure: notRunMessage(d.errors, d.note, trial) }))
      }
    } catch (err) {
      const status = err instanceof HttpError ? err.status : 0
      const failure = status === 403 ? '시험 대화를 돌릴 권한이 없습니다. 관리자에게 요청하세요.'
        : status === 404 ? '이 흐름을 찾지 못했습니다.' : '시험 대화를 보내지 못했습니다. 잠시 뒤 다시 시도하세요.'
      patchLast((t) => ({ ...t, failure }))
    } finally {
      patchLast((t) => ({ ...t, pending: false }))
      setBusy(false)
    }
  }, [flowId, beforeSend])

  const reset = useCallback(() => { sessionRef.current = null; nextN.current = 1; setTurns([]) }, [])
  return { turns, busy, send, reset }
}

export function TestChat({ turns, busy, send, reset, readOnly }: {
  turns: TestTurn[]; busy: boolean; send: (m: string) => void; reset: () => void; readOnly: boolean
}) {
  const [text, setText] = useState('')
  const submit = () => {
    const m = text.trim()
    if (!m || busy || readOnly) return
    setText('')
    send(m)
  }
  return (
    <section className="space-y-2" aria-label="시험 대화">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">저장된 초안으로 돕니다. 배포하지 않아도 되고 실제 넘김·알림은 없습니다. 체험 턴을 1 씁니다.</p>
        <Button type="button" variant="outline" size="sm" onClick={reset} disabled={busy || turns.length === 0}>새 대화</Button>
      </div>
      {readOnly && <p className="border-l-2 border-line-soft pl-2 text-sm text-muted-foreground">관리자만 시험 대화를 돌릴 수 있습니다. 판단 기록은 볼 수 있습니다.</p>}
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
          aria-label="시험 메시지" rows={2} value={text} disabled={readOnly} placeholder="방문자처럼 말해 보세요"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }}
        />
        <Button type="button" onClick={submit} disabled={readOnly || busy || !text.trim()}>보내기</Button>
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
