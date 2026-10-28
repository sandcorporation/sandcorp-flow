import { act } from '@testing-library/react'

/**
 * 이미 시작된 비동기 일(가짜 fetch 응답·상태 갱신)이 모두 끝나도록 마이크로태스크와 React 갱신을 비운다.
 * 「아무 일도 안 일어났음」을 확인하기 전에 쓴다 — 시간(setTimeout)으로 기다리지 않는다(#259).
 */
export async function flushAsync(rounds = 20): Promise<void> {
  await act(async () => {
    for (let i = 0; i < rounds; i++) await Promise.resolve()
  })
}
