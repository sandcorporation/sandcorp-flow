// 노드 배지의 문구 규칙(#241): 마지막 턴의 `node_end` 값을 시간·토큰·비용·핵심 결과·×N 으로 접는다. 비용 문구는 판단 기록과 같은 함수를 쓴다(#235).
import type { LiveNode } from './liveRun'
import { formatCost, formatTokens } from './testTrace'

export type NodeBadge = {
  time: string | null // 「0.8초」「120ms」
  tokens: string | null // 「입력/출력」
  cost: string | null // 「$0.0010」「단가 모름 (모델 X)」
  summary: string | null // 서버가 만든 핵심 결과
  times: string | null // 「×2」 — 한 턴에 여러 번 지났을 때
  text: string // 위를 · 로 이은 한 줄(접근성 이름·툴팁)
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}초`
}

/** 배지에 쓸 값이 없으면(안 끝났거나 오류) null. */
export function nodeBadge(node: LiveNode | undefined): NodeBadge | null {
  const end = node?.end
  if (!node || !end || node.status === 'error') return null
  const used = end.input_tokens + end.output_tokens > 0
  const cost = used || Number(end.cost_usd) > 0 ? formatCost(end.cost_usd, used, end.unpriced_models) : null
  const b = {
    time: formatDuration(end.duration_ms),
    tokens: used ? formatTokens(end.input_tokens, end.output_tokens) : null,
    cost,
    summary: end.summary?.trim() ? end.summary.trim() : null,
    times: node.count > 1 ? `×${node.count}` : null,
  }
  const text = [b.times, b.time, b.tokens && `토큰 ${b.tokens}`, b.cost, b.summary].filter(Boolean).join(' · ')
  return { ...b, text }
}
