// 시험 대화 SSE: POST + Authorization 이라 EventSource 를 못 쓴다 — fetch 스트림을 직접 읽는다.
import { authFetch } from '../auth'
import { HttpError, notifyTrySessionLost } from '../mutator'
import { parseSse, type SseEvent } from './testTrace'

export async function streamTestTurn(
  flowId: string, message: string, sessionId: string | null, onEvent: (e: SseEvent) => void, signal?: AbortSignal,
  base: '/api/tenant' | '/api/try' = '/api/tenant',
): Promise<void> {
  const url = `${base}/flows/${flowId}/test/`
  const body = sessionId ? { message, session_id: sessionId } : { message }
  const init: RequestInit = {
    method: 'POST', signal, body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
  }
  // 체험은 로그인이 없다: Bearer 없이 sf_try 쿠키(HttpOnly)만 실린다.
  const res = base === '/api/try' ? await fetch(url, { ...init, credentials: 'include' }) : await authFetch('agent', url, init)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let parsed: unknown = text
    try { parsed = JSON.parse(text) } catch { /* 본문이 JSON 이 아니면 원문 */ }
    if (base === '/api/try' && res.status === 401 && (parsed as { code?: string } | null)?.code === 'try_no_session') notifyTrySessionLost()
    throw new HttpError(res.status, url, parsed)
  }
  const reader = res.body?.getReader()
  if (!reader) return
  const decoder = new TextDecoder()
  let rest = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    const parsed = parseSse(rest + decoder.decode(value, { stream: true }))
    rest = parsed.rest
    parsed.events.forEach(onEvent)
  }
  parseSse(rest + '\n\n').events.forEach(onEvent)
}
