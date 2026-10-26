// /try 임베드 모드(#180): portfolio 등이 iframe 으로 넣는 `/try?embed=1`.
import { useLocation } from 'react-router-dom'

const KEY = 'sandcorp-flow:embed'
let memo = false

/**
 * `?embed=1` 이면 임베드. SPA 안 이동(템플릿 → /try/flow/:id)은 쿼리를 잃으므로 한 번 켜면 이 문서(탭)에서 유지한다.
 * sessionStorage 는 새로고침을 넘기고, 바깥 링크가 여는 새 탭(noopener)에는 따라가지 않는다 — 쿼리를 모든 링크에 전파하는 것보다
 * 빠뜨릴 곳이 없다. 저장소가 막힌 브라우저에서는 메모리 값만으로 동작한다.
 */
export function detectEmbed(search: string = typeof location === 'undefined' ? '' : location.search): boolean {
  if (new URLSearchParams(search).get('embed') === '1') {
    memo = true
    try { sessionStorage.setItem(KEY, '1') } catch { /* 저장소 차단 — 메모리만 */ }
    return true
  }
  if (memo) return true
  try { return sessionStorage.getItem(KEY) === '1' } catch { return false }
}

export function useEmbed(): boolean {
  return detectEmbed(useLocation().search)
}

export function resetEmbedForTest(): void {
  memo = false
  try { sessionStorage.removeItem(KEY) } catch { /* noop */ }
}

/** 바깥으로 나가는 링크 속성 — 임베드에서는 iframe 안을 떠나지 않고 새 탭으로 연다. */
/** 가입 링크의 속성(ADR-0032 Q4): 크롤러가 따라가 세션을 만들지 않게 nofollow, 임베드는 새 탭이라 noopener 도. */
export const outboundProps = (embed: boolean): { target?: string; rel: string } => (embed ? { target: '_blank', rel: 'noopener nofollow' } : { rel: 'nofollow' })

export const HELLO = 'sandcorp-flow:hello'
export const HEIGHT = 'sandcorp-flow:height'

/**
 * 높이 전달(부모 → hello, 자식 → height). 출처 목록을 화면에 내려보내지 않는다: iframe 이 떴다는 것은 브라우저가 CSP frame-ancestors 로
 * 조상(부모 포함)을 허용했다는 뜻이다. 부모가 먼저 hello 를 보내면 `event.source === window.parent` 일 때만 `event.origin` 을 기억하고,
 * 그 origin 으로만 높이 숫자를 보낸다(`'*'` 와 document.referrer 는 쓰지 않는다). hello 전에는 아무것도 보내지 않는다.
 * SPA 이동에도 살아 있도록 React 밖(main.tsx)에서 모듈 로드 때 한 번 설치한다 — load 이벤트에 보낸 hello 를 놓치지 않는다.
 */
export function installEmbedBridge(): () => void {
  detectEmbed() // 시작 주소의 ?embed=1 을 React 보다 먼저 기억한다
  let parentOrigin: string | null = null
  let observer: ResizeObserver | null = null
  let last = -1

  const send = () => {
    if (!parentOrigin) return
    const root = document.getElementById('root')
    if (!root) return
    const height = Math.ceil(root.getBoundingClientRect().height)
    if (height <= 0 || height === last) return
    last = height
    window.parent.postMessage({ type: HEIGHT, height }, parentOrigin)
  }
  const watch = () => {
    const root = document.getElementById('root')
    if (!observer && root && typeof ResizeObserver === 'function') {
      observer = new ResizeObserver(send)
      observer.observe(root)
    }
  }
  const onMessage = (e: MessageEvent) => {
    if (!detectEmbed('')) return
    if (e.source !== window.parent) return
    if (!e.data || typeof e.data !== 'object' || (e.data as { type?: unknown }).type !== HELLO) return
    if (!e.origin || e.origin === 'null') return // 불투명 출처로는 보낼 수 없다
    parentOrigin = e.origin
    last = -1 // 새 hello(부모가 다시 로드됨)에는 현재 높이를 다시 보낸다
    watch()
    send()
  }
  window.addEventListener('message', onMessage)
  return () => { window.removeEventListener('message', onMessage); observer?.disconnect() }
}

/**
 * 임베드에는 테마 토글이 없다(머리줄 숨김). 대신 호스트 쪽 설정을 따른다: `?theme=dark|light` 가 있으면 그것, 없으면 OS 의 다크 여부.
 * localStorage 에는 쓰지 않는다 — 일반 방문의 테마 선택을 건드리지 않는다. 임베드가 아니면 아무것도 하지 않는다.
 */
export function applyEmbedTheme(search: string = location.search): void {
  if (!detectEmbed(search)) return
  const q = new URLSearchParams(search).get('theme')
  const dark = q === 'dark' || (q !== 'light' && typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
}
