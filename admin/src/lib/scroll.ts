/** 대화 로그 끝으로: 가장 가까운 세로 스크롤 컨테이너만 움직인다. scrollIntoView 는 페이지까지 끌어올려(#90 HITL 진입) 쓰지 않는다. */
export function scrollLogToBottom(el: HTMLElement | null) {
  for (let p = el?.parentElement; p; p = p.parentElement) {
    const oy = getComputedStyle(p).overflowY
    if ((oy === 'auto' || oy === 'scroll') && p.scrollHeight > p.clientHeight) {
      p.scrollTo?.({ top: p.scrollHeight })
      return
    }
    if (p === document.body) return
  }
}
