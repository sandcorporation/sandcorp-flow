import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, within, fireEvent } from '@testing-library/react'
import Landing from './Landing'

describe('Landing (흐름 챗봇 소개)', () => {
  beforeEach(() => {
    document.documentElement.classList.remove('dark', 'light')
    localStorage.clear()
  })

  it('한 줄 소개 제목이 「흐름으로 만드는 상담 챗봇」이다', () => {
    render(<Landing />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('흐름으로 만드는 상담 챗봇')
  })

  it('체험 CTA가 /admin-ui/try 를 가리킨다', () => {
    render(<Landing />)
    expect(screen.getByRole('link', { name: '체험해 보기' })).toHaveAttribute('href', '/admin-ui/try')
  })

  it('템플릿별 체험 링크가 ?template= 짧은 이름을 건다', () => {
    render(<Landing />)
    const sec = screen.getByRole('region', { name: '템플릿으로 바로 체험' })
    const hrefs = within(sec).getAllByRole('link').map(a => a.getAttribute('href'))
    expect(hrefs).toEqual([
      '/admin-ui/try?template=home',
      '/admin-ui/try?template=shop',
      '/admin-ui/try?template=academy',
    ])
  })

  it('운영자·테넌트 로그인 링크가 각 진입 경로를 가리킨다', () => {
    render(<Landing />)
    const sec = screen.getByRole('region', { name: '로그인' })
    expect(within(sec).getByRole('link', { name: /^운영자 로그인/ })).toHaveAttribute('href', '/admin-ui/')
    expect(within(sec).getByRole('link', { name: /^테넌트 로그인/ })).toHaveAttribute('href', '/admin-ui/tenant')
  })

  it('실제 있는 기능만 소개한다: 흐름 편집·시험 대화·판단 기록·HTTP 도구', () => {
    render(<Landing />)
    const sec = screen.getByRole('region', { name: '기능' })
    for (const t of ['흐름 편집기', '시험 대화', '판단 기록', 'HTTP 도구']) {
      expect(within(sec).getByRole('heading', { name: t })).toBeInTheDocument()
    }
  })

  it('문의는 샌드코프 홈 문의 링크 하나뿐이고 이메일·전화 링크가 없다', () => {
    const { container } = render(<Landing />)
    const inq = screen.getByRole('link', { name: /도입이 궁금하면 문의/ })
    expect(inq).toHaveAttribute('href', 'https://sandcorp.co.kr/#home-inquiry')
    expect(container.querySelectorAll('a[href^="mailto:"], a[href^="tel:"]')).toHaveLength(0)
    // 본문(main)에는 이메일 글자도 없다. 푸터의 사업자 표시(법적 의무)는 별개.
    expect(container.querySelector('main')!.textContent).not.toMatch(/\S+@\S+\.\S+/)
  })

  it('옛 챗 데모·지식그래프 데모가 없다', () => {
    const { container } = render(<Landing />)
    expect(container.querySelector('[data-testid="hero-demo"]')).toBeNull()
    expect(screen.queryByText(/노드를 눌러/)).toBeNull()
  })

  it('테마 전환 버튼이 .dark 를 토글하고 기억한다', () => {
    render(<Landing />)
    const btn = screen.getByRole('button', { name: '테마 전환' })
    fireEvent.click(btn)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('admin_theme')).toBe('dark')
    fireEvent.click(btn)
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(document.documentElement.classList.contains('light')).toBe(true)
    expect(localStorage.getItem('admin_theme')).toBe('light')
  })
})
