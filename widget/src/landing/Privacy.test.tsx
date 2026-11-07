import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import Privacy from './Privacy'

const ERRORS_DOC = 'https://sandcorporation.github.io/privacy-and-terms/errors/'

describe('Privacy (처리방침) — 서비스 오류 기록 (#312)', () => {
  it('수집 항목에 서비스 오류 기록(자동 수집)이 있다', () => {
    render(<Privacy />)
    expect(screen.getByText(/서비스 오류 기록\(자동 수집\)/)).toBeInTheDocument()
  })

  it('보관 90일 자동 삭제, 직접 운영 국내 서버, 위탁·제3자 제공·국외 이전 없음을 밝힌다', () => {
    const { container } = render(<Privacy />)
    const text = container.textContent ?? ''
    expect(text).toContain('90일')
    expect(text).toContain('자동으로 삭제')
    expect(text).toContain('직접 운영하는 국내 서버')
    expect(text).toContain('위탁·제3자 제공·국외 이전 없음')
  })

  it('지우는 것(쿠키·토큰·비밀번호·요청 본문)을 밝히고 호스트·IP는 싣지 않는다', () => {
    const { container } = render(<Privacy />)
    const text = container.textContent ?? ''
    for (const w of ['쿠키', '인증 헤더', '비밀번호', '요청 본문']) expect(text).toContain(w)
    expect(text).not.toMatch(/glitchtip|\d+\.\d+\.\d+\.\d+/i)
  })

  it('공통 문서 링크가 새 창이면 rel=noopener noreferrer 다', () => {
    render(<Privacy />)
    const a = screen.getByRole('link', { name: /서비스 오류 기록/ })
    expect(a).toHaveAttribute('href', ERRORS_DOC)
    expect(a).toHaveAttribute('target', '_blank')
    expect(a).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('개정 이력 한 줄이 처리방침의 변경 절에 있다', () => {
    render(<Privacy />)
    expect(screen.getByText(/개정 이력: 2026\. 11\. 7\. — 서비스 오류 기록 항목 추가/)).toBeInTheDocument()
  })
})
