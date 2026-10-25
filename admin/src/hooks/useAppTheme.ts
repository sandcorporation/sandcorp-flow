import { useSyncExternalStore } from 'react'

// 앱 테마(ADR-0017)는 documentElement 의 .dark 클래스다. 그 클래스를 구독해 한 자리에서 읽는다 —
// 테마 버튼이 바꾸든 부팅 때 저장값이 적용되든 즉시 따라간다. OS 설정(prefers-color-scheme)은 보지 않는다(#157).
function subscribe(onChange: () => void) {
  const mo = new MutationObserver(onChange)
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  return () => mo.disconnect()
}
const getTheme = (): 'light' | 'dark' => (document.documentElement.classList.contains('dark') ? 'dark' : 'light')

export function useAppTheme(): 'light' | 'dark' {
  return useSyncExternalStore(subscribe, getTheme, () => 'light')
}
