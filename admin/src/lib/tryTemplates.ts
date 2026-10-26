// /try 의 템플릿 이름·시작 호출(#37).
import { HttpError } from '../mutator'

/** `?template=home` 같은 짧은 이름 → 템플릿 id. id 를 그대로 주어도 된다. */
const SHORT: Record<string, string> = { home: 'sandcorp_home', shop: 'shop_support', academy: 'academy_reservation' }
export const templateId = (q: string | null): string | null => (q ? (SHORT[q] ?? q) : null)
/** 템플릿 id → `?template=` 에 쓸 짧은 이름(없으면 id 그대로). */
export const shortTemplate = (id: string): string => Object.keys(SHORT).find((k) => SHORT[k] === id) ?? id

/** 카드에 곁들이는 한 줄 설명(서버 목록엔 이름·문서만 있다). */
export const BLURB: Record<string, string> = {
  sandcorp_home: '회사 질문은 지식 검색으로 답하고, 문의 방법은 문의 폼으로 안내하는 홈 상담.',
  shop_support: '주문 조회·환불 규정 안내, 필요하면 사람에게 넘기는 쇼핑몰 고객 지원.',
  academy_reservation: '레벨 테스트 예약을 도구로 잡아 주는 학원 상담.',
}

export const TRY_ERRORS: Record<string, string> = {
  try_ip_limit: '오늘 이 네트워크에서 만들 수 있는 체험을 모두 만들었어요. 내일 다시 찾아 주세요.',
}

/** 체험 시작 실패를 사람이 읽을 사유로. */
export function tryFailureText(e: unknown): string {
  const body = e instanceof HttpError ? (e.body as { detail?: string; code?: string } | undefined) : undefined
  return body?.detail || (body?.code && TRY_ERRORS[body.code]) || '체험을 시작하지 못했습니다. 잠시 뒤 다시 시도하세요.'
}
