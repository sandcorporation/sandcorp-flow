// 체험 시험 대화가 못 돈 사유(SSE done.code, #135) → 화면 문구. 코드가 없으면(편집기·일반 실패) null.
export type TryRefusal = { code: string; title: string; text: string; /** 이 세션에선 더 보내지 못한다 */ closed: boolean }

export function tryRefusal(code: string | null | undefined, note: string): TryRefusal | null {
  switch (code) {
    case 'try_daily_budget':
      return { code, title: '내일 다시 와 주세요', text: note || '오늘 체험 대화가 모두 찼어요. 편집은 계속할 수 있어요.', closed: false }
    case 'try_turns_exhausted':
      return { code, title: '체험 턴을 모두 썼어요', text: '가입하면 내 흐름으로 계속 시험할 수 있어요. 편집은 계속할 수 있어요.', closed: true }
    case 'try_unavailable':
      return { code, title: '체험을 잠시 쉬고 있어요', text: note || '지금은 체험 대화를 준비하고 있어요. 잠시 후 다시 찾아 주세요.', closed: false }
    default:
      return null
  }
}
