import { describe, it, expect, afterEach } from 'vitest'
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

describe('푸터 전화번호 — 실행 설정 LANDING_TEL (#326)', () => {
  afterEach(() => { window.__FLOW_RUNTIME__ = undefined })

  it('실행 설정에 번호가 있으면 「전화 …」 줄을 그린다', () => {
    window.__FLOW_RUNTIME__ = { landingTel: '02-0000-0000' }
    render(<SiteFooter />)
    expect(screen.getByText('전화 02-0000-0000')).toBeInTheDocument()
  })

  it('비어 있거나 설정이 없으면 전화 줄을 그리지 않는다', () => {
    for (const cfg of [{ landingTel: '' }, {}, undefined]) {
      window.__FLOW_RUNTIME__ = cfg
      const { unmount } = render(<SiteFooter />)
      expect(screen.queryByText(/전화/)).toBeNull()
      unmount()
    }
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

// 빌드 입력 HTML 원문(vite 가 파일 시스템에서 읽는다).
const pages = import.meta.glob<string>('../../*.html', { query: '?raw', import: 'default', eager: true })
const pageHtml = (f: string) => pages[`../../${f}.html`]

const MADE_BY = 'https://sandcorp.co.kr/?utm_source=flow&utm_medium=footer'

describe('Made by Sandcorp 링크 (#288)', () => {
  it('푸터에 utm 주소로 달고 rel(nofollow·sponsored)을 붙이지 않는다', () => {
    render(<SiteFooter />)
    const a = screen.getByRole('link', { name: 'Made by Sandcorp' })
    expect(a).toHaveAttribute('href', MADE_BY)
    expect(a.hasAttribute('rel')).toBe(false)
  })

  it('서버 HTML(landing·privacy·terms)의 #root 안에도 같은 링크가 있어 크롤러가 본다', () => {
    for (const f of ['landing', 'privacy', 'terms']) {
      const html = pageHtml(f)
      const root = html.match(/<div id="root">([\s\S]*?)<\/div>\s*<script/)?.[1] ?? ''
      expect(root, f).toContain(`<a href="${MADE_BY.replace('&', '&amp;')}">Made by Sandcorp</a>`)
      expect(root, f).not.toMatch(/rel=/)
    }
  })

  it('챗봇 위젯 진입 HTML에는 링크가 없다', () => {
    expect(pageHtml('index')).not.toContain('sandcorp.co.kr')
  })
})
