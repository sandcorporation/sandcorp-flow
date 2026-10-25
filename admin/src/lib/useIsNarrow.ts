import { useEffect, useState } from 'react'

/** 폰 폭 기준(px). 이 값 미만이 「폰」 — Tailwind `md`(768, 대시보드 사이드바가 펼쳐지는 분기점)와 같다. */
export const NARROW_MAX = 767
const QUERY = `(max-width: ${NARROW_MAX}px)`

/**
 * 폰 폭인지 한 자리에서 정한다(#38). 폰에서는 캔버스를 읽기만 하고(팬·줌·노드 누르기), 선 잇기·노드 추가·옮기기·삭제는 PC에서만 한다.
 * 편집기는 이 값 하나로 React Flow props 와 팔레트 노출을 끈다. matchMedia 가 없는 환경(jsdom)에서는 항상 넓은 폭이다.
 */
export function useIsNarrow(): boolean {
  const mq = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(QUERY) : null)
  const [narrow, setNarrow] = useState(() => mq()?.matches ?? false)
  useEffect(() => {
    const m = mq()
    if (!m) return
    const on = () => setNarrow(m.matches)
    on()
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return narrow
}
