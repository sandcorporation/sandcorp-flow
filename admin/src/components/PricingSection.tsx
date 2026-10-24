import { useEffect, useState } from 'react'
import { getTrial, type TrialState } from '../api'
import { Card, CardContent } from '@/components/ui/card'

const STATE_TEXT: Record<TrialState, (r: number, l: number) => string> = {
  trial: (r, l) => `플랫폼 체험 중 — 남은 대화 ${r}/${l}턴`,
  exhausted: () => '체험 한도를 다 썼습니다 — LLM 키를 넣으면 다시 답합니다',
  unavailable: () => '플랫폼 체험이 준비되지 않았습니다 — LLM 키를 넣으면 바로 씁니다',
  byok: () => '내 LLM 키로 쓰는 중 — 대화 수 한도 없음',
  platform_default: () => '개발 환경 기본 제공자로 쓰는 중',
}

// 요금(#41): 결제는 다음 판(ADR-0026). 지금은 「준비 중」과 이 조직의 체험 상태만 보인다.
export default function PricingSection() {
  const [status, setStatus] = useState('')

  useEffect(() => {
    getTrial()
      .then((t) => setStatus(STATE_TEXT[t.state]?.(t.remaining, t.limit) ?? ''))
      .catch(() => setStatus(''))
  }, [])

  return (
    <div className="space-y-4 break-keep" data-testid="pricing-section">
      <Card>
        <CardContent className="space-y-2 p-4">
          <p className="font-medium">요금제는 준비 중입니다.</p>
          <p className="text-sm leading-relaxed text-muted-foreground">
            지금은 무료 베타입니다. 새 조직은 플랫폼 키로 손님 대화를 체험하고, 그 뒤에는 설정의 「AI 모델」에서
            내 LLM 키를 넣어 씁니다. LLM 사용료는 키를 낸 공급자(OpenAI 등)에 직접 냅니다.
          </p>
        </CardContent>
      </Card>
      {status && (
        <p className="text-sm text-muted-foreground" data-testid="pricing-trial-status">이 조직: {status}</p>
      )}
    </div>
  )
}
