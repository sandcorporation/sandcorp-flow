import { describe, it, expect } from 'vitest'
import { compactTokens, trendDots } from './usageChart'

describe('사용량 차트 축·점 설정 (#103)', () => {
  it('토큰 눈금은 K·M 으로 줄여 읽기 쉽게 한다', () => {
    expect(compactTokens(0)).toBe('0')
    expect(compactTokens(950)).toBe('950')
    expect(compactTokens(300000)).toBe('300K')
    expect(compactTokens(1200000)).toBe('1.2M')
    expect(compactTokens(1234567)).toBe('1.23M')
    expect(compactTokens(2500)).toBe('2.5K')
  })

  it('추이 선은 점이 적어 선분이 안 보일 때만 점을 찍는다', () => {
    expect(trendDots(1)).toBe(true)
    expect(trendDots(2)).toBe(true)
    expect(trendDots(3)).toBe(false)
    expect(trendDots(30)).toBe(false)
  })
})
