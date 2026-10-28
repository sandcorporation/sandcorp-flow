// 고정 대기 대신 쓰는 사건 기다리기 도우미(#259).
// 둘 다 시간 값이 없다 — 화면이 가라앉았다는 신호(프레임마다 보는 값이 변하지 않음)와 요청이 모두 끝났다는 신호로 풀린다.

/**
 * 화면이 가라앉을 때까지 기다린다 — 맞춤 보기(react-flow 뷰포트 변환)·스크롤(window·main)·진행 중인 전환이
 * 연속 여러 프레임 동안 변하지 않을 때까지. 스크린샷 직전, 위치·크기를 재기 직전에 쓴다.
 * page 와 frame 모두 받는다.
 */
async function settled(pageOrFrame, stableFrames = 6) {
  await pageOrFrame.evaluate((need) => new Promise((resolve) => {
    const sig = () => {
      const vp = document.querySelector('.react-flow__viewport')
      const scrolls = [...document.querySelectorAll('main, [data-log-scroll]')].map((m) => m.scrollTop).join(',')
      const running = document.getAnimations().filter((a) => a.playState === 'running' && a.effect?.getComputedTiming().iterations !== Infinity).length
      return [vp ? getComputedStyle(vp).transform : '', window.scrollY, scrolls, running, innerWidth, innerHeight].join('|')
    }
    let last = sig()
    let stable = 0
    const tick = () => {
      const now = sig()
      stable = now === last ? stable + 1 : 0
      last = now
      if (stable >= need) resolve()
      else requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }), stableFrames)
}

const frames = (page, n = 2) => page.evaluate((k) => new Promise((resolve) => {
  const step = (i) => (i === 0 ? resolve() : requestAnimationFrame(() => step(i - 1)))
  step(k)
}), n)

/**
 * 이 page 가 보낸 요청이 모두 끝났는지 지켜본다. 이동(goto)·동작 **전에** 만들어 둔다.
 * `idle()` 은 보낸 요청이 전부 응답·실패로 끝날 때까지 기다린다(SSE 처럼 응답 머리만 오고 계속 열린 스트림은 제외).
 * 「저장이 끝났다」「아무 요청도 더 안 나갔다」를 시간 추측 없이 확인한다. network idle 의 안전한 대용이다.
 */
function trackRequests(page) {
  const live = new Set()
  let waiters = []
  const done = (req) => {
    live.delete(req)
    if (live.size === 0) { const w = waiters; waiters = []; w.forEach((f) => f()) }
  }
  page.on('request', (r) => live.add(r))
  page.on('requestfinished', done)
  page.on('requestfailed', done)
  page.on('response', (res) => { if ((res.headers()['content-type'] || '').includes('text/event-stream')) done(res.request()) })
  return {
    async idle() {
      // 응답이 새 요청(이펙트)을 부를 수 있으니, 프레임을 지난 뒤에도 비어 있을 때까지 되풀이한다.
      do {
        await frames(page)
        if (live.size > 0) await new Promise((resolve) => waiters.push(resolve))
      } while (live.size > 0)
    },
  }
}

module.exports = { settled, trackRequests, frames }
