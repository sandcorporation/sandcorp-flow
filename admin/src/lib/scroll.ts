/**
 * 대화 로그 끝으로: `data-log-scroll` 표시가 붙은 조상(카드 안 로그 컨테이너)만 맨 아래로 맞춘다.
 * scrollIntoView 는 모든 스크롤 조상을 움직이고(#90 window, #106 셸 <main>), "가장 가까운 넘치는 조상"을 찾는 방식은
 * 로그가 안 넘칠 때 <main> 까지 올라가 끌어내렸다. 표시된 컨테이너만 직접 scrollTop 을 맞추면 페이지·셸은 건드리지 않는다.
 */
export function scrollLogToBottom(el: HTMLElement | null) {
  for (let p = el?.parentElement; p; p = p.parentElement) {
    if (p.hasAttribute('data-log-scroll')) p.scrollTop = p.scrollHeight
  }
}
