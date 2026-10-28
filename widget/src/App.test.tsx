import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import App, { resolveSlug, resolveFlowKey } from './App'
import type { ChatTransport } from './transport'

const mockTransport: ChatTransport = {
  createEventSource: () => ({ addEventListener() {}, close() {}, onerror: null }),
  postMessage: async () => {},
}

// jsdom의 window.location.pathname을 테스트마다 교체한다(공개 URL 시뮬레이션).
function setPath(pathname: string) {
  Object.defineProperty(window, 'location', {
    value: { pathname, search: '' },
    writable: true,
    configurable: true,
  })
}

describe('App — 무효 slug 진입 가드', () => {
  it('존재하지 않는 slug면 not-found를 띄우고 챗 위젯을 렌더하지 않는다', async () => {
    setPath('/chatbot/ghost-shop/')
    render(<App transport={mockTransport} resolveTenant={async () => 'notfound'} />)
    expect(await screen.findByText(/존재하지 않는 챗봇/)).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('메시지를 입력하세요...')).toBeNull()
  })

  it('유효한 slug면 챗 위젯을 렌더한다', async () => {
    setPath('/chatbot/real-shop/')
    render(<App transport={mockTransport} resolveTenant={async () => 'valid'} />)
    expect(await screen.findByPlaceholderText('메시지를 입력하세요...')).toBeInTheDocument()
  })
})

describe('resolveSlug (한글 공개 챗봇 URL — issue 187)', () => {
  it('percent-encoded 한글 path에서 NFC 슬러그를 추출한다', () => {
    setPath('/chatbot/' + encodeURIComponent('우리가게') + '/')
    expect(resolveSlug()).toBe('우리가게')
  })

  it('NFD(자모분리) 입력을 NFC로 정규화한다', () => {
    const nfd = '강남'.normalize('NFD')
    expect(nfd).not.toBe('강남'.normalize('NFC'))          // precondition
    setPath('/chatbot/' + encodeURIComponent(nfd) + '/')
    expect(resolveSlug()).toBe('강남'.normalize('NFC'))
  })

  it('평문 한글 path도 추출한다', () => {
    setPath('/chatbot/우리가게/')
    expect(resolveSlug()).toBe('우리가게')
  })

  it('기존 ASCII 슬러그를 유지한다(하위호환)', () => {
    setPath('/chatbot/abc-shop/')
    expect(resolveSlug()).toBe('abc-shop')
  })

  it('chatbot 경로가 아니면 빈 문자열', () => {
    setPath('/something/else/')
    expect(resolveSlug()).toBe('')
  })
})

describe('흐름 키 주소 /chatbot/<slug>/<key>/ (#246)', () => {
  it('두 번째 조각을 흐름 키로 읽는다', () => {
    setPath('/chatbot/shop/ab12cd34/')
    expect(resolveSlug()).toBe('shop')
    expect(resolveFlowKey()).toBe('ab12cd34')
  })

  it('한 조각짜리 기존 주소는 키가 없다(기본 흐름)', () => {
    setPath('/chatbot/shop/')
    expect(resolveFlowKey()).toBe('')
  })

  it('키를 진입 가드에 넘기고, 404면 「열려 있지 않음」을 보이고 입력을 막는다', async () => {
    setPath('/chatbot/shop/ab12cd34/')
    const calls: [string, string][] = []
    render(<App transport={mockTransport} resolveTenant={async (slug, key) => { calls.push([slug, key]); return 'notfound' }} />)
    expect(await screen.findByText('지금 열려 있지 않은 상담입니다.')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('메시지를 입력하세요...')).toBeNull()
    expect(calls).toEqual([['shop', 'ab12cd34']])
  })

  it('기존 주소는 빈 키로 가드를 부른다', async () => {
    setPath('/chatbot/shop/')
    const calls: [string, string][] = []
    render(<App transport={mockTransport} resolveTenant={async (slug, key) => { calls.push([slug, key]); return 'valid' }} />)
    expect(await screen.findByPlaceholderText('메시지를 입력하세요...')).toBeInTheDocument()
    expect(calls).toEqual([['shop', '']])
  })
})
