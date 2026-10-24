import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { getTrial, type TrialState } from '../api'
import { cn } from '@/lib/utils'
import { useIsAdmin } from '../lib/useAgentRole'

type Trial = { state: TrialState; remaining: number; limit: number }

// 체험 한도(#41) 안내 — 자기 LLM 키가 없는 조직에만 보인다(BYOK·dev 기본 제공자면 숨김).
// 방문자에게는 내부 사정을 말하지 않으므로, 한도·키 이야기는 이 관리 화면에서만 한다.
export function trialMessage(t: Trial): { tone: 'info' | 'warn'; text: string } | null {
  switch (t.state) {
    case 'trial':
      return { tone: 'info', text: `플랫폼 체험 중 — 남은 대화 ${t.remaining}/${t.limit}턴. LLM 키를 넣으면 한도 없이 씁니다.` }
    case 'exhausted':
      return { tone: 'warn', text: '체험 한도를 다 썼습니다 — 설정에서 LLM 키를 넣어 주세요. 그동안 손님에게는 「상담 준비 중」으로 안내됩니다.' }
    case 'unavailable':
      return { tone: 'warn', text: '플랫폼 체험이 준비되지 않았습니다 — 설정에서 LLM 키를 넣으면 바로 쓸 수 있습니다.' }
    default:
      return null
  }
}

// 시험 대화 턴이 끝나면 체험 횟수가 올랐으므로 배너가 다시 읽게 알린다(#103). 방문자 위젯 턴은 대상이 아니다.
const TRIAL_CHANGED = 'flow:trial-changed'
export const notifyTrialChanged = () => window.dispatchEvent(new Event(TRIAL_CHANGED))

export default function TrialBanner() {
  const isAdmin = useIsAdmin()
  const [trial, setTrial] = useState<Trial | null>(null)
  const { pathname, search } = useLocation()  // 화면을 옮길 때마다 다시 읽는다(키를 넣고 나면 사라지게)

  useEffect(() => {
    getTrial().then(setTrial).catch(() => setTrial(null))
  }, [pathname, search])

  useEffect(() => {
    const reload = () => { getTrial().then(setTrial).catch(() => setTrial(null)) }
    window.addEventListener(TRIAL_CHANGED, reload)
    return () => window.removeEventListener(TRIAL_CHANGED, reload)
  }, [])

  const msg = trial && trialMessage(trial)
  if (!msg) return null
  return (
    <div
      role={msg.tone === 'warn' ? 'alert' : 'status'}
      data-testid="trial-banner"
      className={cn(
        'mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2 text-sm break-keep',
        msg.tone === 'warn'
          ? 'border-primary bg-primary/15 text-foreground'
          : 'border-border bg-muted/50 text-muted-foreground',
      )}
    >
      <span className="min-w-0 flex-1">{msg.text}</span>
      {isAdmin ? (
        <Link to="/tenant/config?section=ai" className="shrink-0 font-medium text-foreground underline decoration-primary decoration-2 underline-offset-4">
          LLM 키 넣기
        </Link>
      ) : (
        <span className="shrink-0 text-xs">관리자에게 알려 주세요.</span>
      )}
    </div>
  )
}
