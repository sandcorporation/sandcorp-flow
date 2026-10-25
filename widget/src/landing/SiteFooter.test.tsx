import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import SiteFooter from './SiteFooter'
import Privacy from './Privacy'
import Terms from './Terms'

describe('SiteFooter (사업자 정보 + 링크)', () => {
  it('푸터 로고에 Flow 아이콘이 붙는다 (#142)', () => {
    const { container } = render(<SiteFooter />)
    expect(container.querySelector('.footer-logo img[src$="flow/icon.svg"]')).not.toBeNull()
  })

  it('약관·처리방침 머리 홈 링크에도 아이콘이 붙는다 (#142)', () => {
    for (const Page of [Terms, Privacy]) {
      const { container, unmount } = render(<Page />)
      expect(container.querySelector('a.doc-home img[src$="flow/icon.svg"]')).not.toBeNull()
      unmount()
    }
  })

  it('정보통신망법 사업자 정보를 표시한다', () => {
    render(<SiteFooter />)
    expect(screen.getByText('샌드코프')).toBeInTheDocument()
    expect(screen.getByText(/사업자등록번호 752-26-01740/)).toBeInTheDocument()
    expect(screen.getByText(/대표 김한얼/)).toBeInTheDocument()
    expect(screen.getByText(/개인정보보호책임자 김한얼/)).toBeInTheDocument()
  })

  it('이용약관·개인정보처리방침 링크를 단다', () => {
    render(<SiteFooter />)
    expect(screen.getByRole('link', { name: '이용약관' })).toHaveAttribute('href', '/terms')
    expect(screen.getByRole('link', { name: '개인정보처리방침' })).toHaveAttribute('href', '/privacy')
  })
})

describe('푸터 소개 문구', () => {
  it('옛 「지식그래프 기반」 문구 대신 흐름 챗봇 문구를 쓴다', () => {
    render(<SiteFooter />)
    expect(screen.getByText('흐름으로 만드는 상담 챗봇')).toBeInTheDocument()
    expect(screen.queryByText(/지식그래프 기반/)).toBeNull()
  })
})

describe('약관·처리방침 페이지', () => {
  it('Privacy가 개인정보처리방침 제목과 보호책임자를 렌더한다', () => {
    render(<Privacy />)
    expect(screen.getByRole('heading', { name: '개인정보처리방침', level: 1 })).toBeInTheDocument()
    expect(screen.getByText(/성명: 김한얼/)).toBeInTheDocument()
  })

  it('Terms가 이용약관 제목과 목적 조항을 렌더한다', () => {
    render(<Terms />)
    expect(screen.getByRole('heading', { name: '이용약관', level: 1 })).toBeInTheDocument()
    expect(screen.getByText(/제1조 \(목적\)/)).toBeInTheDocument()
  })
})
