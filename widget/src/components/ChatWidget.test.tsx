import { describe, it, expect } from 'vitest'
import { createRef } from 'react'
import { render, act } from '@testing-library/react'
import ChatWidget from './ChatWidget'
import type { ChatWidgetHandle } from './ChatWidget'
import type { ChatTransport, EventSourceLike } from '../transport'

// 결정적 mock 트랜스포트 — 전역 EventSource 덮어쓰기 대신 주입한다(175: 트랜스포트 주입).
class MockEventSource implements EventSourceLike {
  listeners: Record<string, ((e: { data: string }) => void)[]> = {}
  onerror: ((ev: unknown) => void) | null = null
  addEventListener(type: string, fn: (e: { data: string }) => void) {
    (this.listeners[type] ||= []).push(fn)
  }
  close() {}
  emit(type: string, data: unknown) {
    (this.listeners[type] || []).forEach(fn => fn({ data: JSON.stringify(data) }))
  }
}

function makeTransport() {
  const es = new MockEventSource()
  const sent: { sessionId: string; content: string }[] = []
  const transport: ChatTransport = {
    createEventSource: () => es,
    postMessage: async (sessionId, content) => { sent.push({ sessionId, content }) },
  }
  return { es, sent, transport }
}

describe('ChatWidget (주입 트랜스포트)', () => {
  it('assistant 메시지는 마크다운으로, user 메시지는 평문으로 렌더한다', () => {
    const { es, transport } = makeTransport()
    const { container } = render(<ChatWidget slug="t" visitorId="v" transport={transport} />)
    act(() => {
      es.emit('connected', {
        session_id: 's1',
        history: [
          { role: 'assistant', content: '**굵게** 답변' },
          { role: 'user', content: '**평문** 질문' },
        ],
      })
    })

    const assistantBubble = container.querySelector('[data-role="assistant"]')!
    expect(assistantBubble.querySelector('strong')).toHaveTextContent('굵게')

    const userBubble = container.querySelector('[data-role="user"]')!
    expect(userBubble.querySelector('strong')).toBeNull()
    expect(userBubble).toHaveTextContent('**평문** 질문')
  })

  it('스트리밍 토큰이 누적되며 라이브로 마크다운 렌더된다(닫히면 서식)', () => {
    const { es, transport } = makeTransport()
    render(<ChatWidget slug="t" visitorId="v" transport={transport} />)
    act(() => { es.emit('connected', { session_id: 's1' }) })

    act(() => { es.emit('token', { content: '**굵게' }) })
    expect(document.querySelector('strong')).toBeNull()

    act(() => { es.emit('token', { content: '** 입니다' }) })
    expect(document.querySelector('strong')).toHaveTextContent('굵게')
  })

  it('imperative send(text)로 메시지를 보낸다(추천 칩용)', () => {
    const { es, sent, transport } = makeTransport()
    const ref = createRef<ChatWidgetHandle>()
    render(<ChatWidget ref={ref} slug="t" visitorId="v" transport={transport} />)
    act(() => { es.emit('connected', { session_id: 's1' }) })

    act(() => { ref.current!.send('안녕') })

    expect(sent).toEqual([{ sessionId: 's1', content: '안녕' }])
  })
})

describe('ChatWidget — 흐름 키(#246)', () => {
  it('키가 있으면 stream 주소에 flow=를 붙이고, 없으면 붙이지 않는다', () => {
    const urls: string[] = []
    const transport: ChatTransport = {
      createEventSource: (url) => { urls.push(url); return new MockEventSource() },
      postMessage: async () => {},
    }
    render(<ChatWidget slug="shop" visitorId="v" flowKey="ab12cd34" transport={transport} />)
    render(<ChatWidget slug="shop" visitorId="v" transport={transport} />)
    expect(urls[0]).toContain('&flow=ab12cd34')
    expect(urls[1]).not.toContain('flow=')
  })

  it('메시지가 「열려 있지 않음」으로 막히면 안내를 보이고 입력을 막는다', async () => {
    const es = new MockEventSource()
    const transport: ChatTransport = {
      createEventSource: () => es,
      postMessage: async () => 'closed',
    }
    const ref = createRef<ChatWidgetHandle>()
    const { findByText, getByPlaceholderText } = render(
      <ChatWidget ref={ref} slug="shop" visitorId="v" flowKey="ab12cd34" transport={transport} />
    )
    act(() => { es.emit('connected', { session_id: 's1' }) })
    await act(async () => { ref.current!.send('안녕') })
    expect(await findByText('지금 열려 있지 않은 상담입니다.')).toBeTruthy()
    expect((getByPlaceholderText('메시지를 입력하세요...') as HTMLTextAreaElement).disabled).toBe(true)
  })
})
