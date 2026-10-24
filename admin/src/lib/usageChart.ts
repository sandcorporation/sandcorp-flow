// 사용량 차트(recharts) 설정 도우미 — 테넌트·Operator 화면이 같이 쓴다.

/** 토큰 눈금을 K·M 으로 줄인다(1200000 → 1.2M). 눈금 글자가 길어 그래프 폭을 먹지 않게 한다. */
export function compactTokens(n: number): string {
  const fmt = (v: number, unit: string) => `${Number(v.toFixed(2))}${unit}`
  if (Math.abs(n) >= 1_000_000) return fmt(n / 1_000_000, 'M')
  if (Math.abs(n) >= 1_000) return fmt(n / 1_000, 'K')
  return String(n)
}

/** 날짜가 하루·이틀뿐이면 선분이 거의 안 보이므로 점을 함께 찍는다. */
export const trendDots = (points: number): boolean => points <= 2
