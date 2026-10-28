import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from 'react'
import type { CSSProperties } from 'react'
import Markdown from './Markdown'
import { realTransport, API_BASE, flowParam } from '../transport'
import type { ChatTransport, EventSourceLike } from '../transport'

interface ChatMessage {
  role: string
  content: string
  agentName?: string
}

type TypingActor = null | 'ai' | 'human_agent'
type Status = 'connecting' | 'ready' | 'streaming' | 'error' | 'closed'

export const CLOSED_NOTICE = '지금 열려 있지 않은 상담입니다.'

interface ChatWidgetProps {
  slug: string
  visitorId: string
  hash?: string
  /** 흐름 키 주소(/chatbot/<slug>/<key>/)의 키. 없으면 기본 흐름(#246). */
  flowKey?: string
  /** 주입 가능한 트랜스포트(기본=실제 백엔드). 랜딩 데모는 mock으로 토큰 스트리밍을 연출한다. */
  transport?: ChatTransport
}

/** 외부에서 메시지를 보낼 수 있는 imperative 핸들(랜딩 데모의 추천 칩 클릭용). */
export interface ChatWidgetHandle {
  send(text: string): void
}

const ChatWidget = forwardRef<ChatWidgetHandle, ChatWidgetProps>(function ChatWidget(
  { slug, visitorId, hash = '', flowKey = '', transport = realTransport }, ref
) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [status, setStatus] = useState<Status>('connecting')
  const [streamingText, setStreamingText] = useState('')
  const [isHitl, setIsHitl] = useState(false)
  const [brandName, setBrandName] = useState('')
  const [typingActor, setTypingActor] = useState<TypingActor>(null)
  const typingTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const eventSourceRef = useRef<EventSourceLike | null>(null)

  useEffect(() => {
    const es = transport.createEventSource(
      `${API_BASE}/api/chat/stream?slug=${encodeURIComponent(slug)}&visitor_id=${encodeURIComponent(visitorId)}&hash=${encodeURIComponent(hash)}${flowParam(flowKey)}`
    )
    eventSourceRef.current = es

    es.addEventListener('connected', (e) => {
      const data = JSON.parse(e.data)
      setSessionId(data.session_id)
      setStatus('ready')
      if (data.brand_name) setBrandName(data.brand_name)
      if (data.history) {
        setMessages(data.history)
        if (data.is_hitl) setIsHitl(true)
      } else if (data.welcome_message) {
        setMessages(msgs => [...msgs, { role: 'assistant', content: data.welcome_message }])
      }
    })

    es.addEventListener('token', (e) => {
      const data = JSON.parse(e.data)
      setTypingActor(null)
      setStreamingText(prev => prev + data.content)
    })

    es.addEventListener('done', () => {
      setTypingActor(null)
      setStreamingText(prev => {
        if (prev) {
          setMessages(msgs => [...msgs, { role: 'assistant', content: prev }])
        }
        return ''
      })
      setStatus('ready')
    })

    es.addEventListener('typing', (e) => {
      const data = JSON.parse(e.data)
      setTypingActor(data.actor)
      clearTimeout(typingTimerRef.current)
      typingTimerRef.current = setTimeout(() => setTypingActor(null), 3000)
    })

    es.addEventListener('hitl_start', () => {
      setIsHitl(true)
      setTypingActor(null)
      setStreamingText('')
      setStatus('ready')
      setMessages(msgs => [...msgs, {
        role: 'system',
        content: '잠시만 기다려 주세요. 상담원과 연결 중입니다.',
      }])
    })

    es.addEventListener('hitl_message', (e) => {
      const data = JSON.parse(e.data)
      setTypingActor(null)
      clearTimeout(typingTimerRef.current)
      setMessages(msgs => [...msgs, {
        role: 'human_agent',
        content: data.content,
        agentName: data.agent_display_name || '상담원',
      }])
    })

    es.addEventListener('hitl_end', () => {
      setIsHitl(false)
      setMessages(msgs => [...msgs, {
        role: 'system',
        content: 'AI 상담으로 전환되었습니다.',
      }])
    })

    es.addEventListener('error', () => setStatus('error'))

    es.onerror = () => {
      setStatus('error')
      es.close()
    }

    return () => {
      es.close()
      clearTimeout(typingTimerRef.current)
    }
  }, [slug, visitorId, hash, flowKey, transport])

  useEffect(() => {
    // 위젯 내부 메시지 영역만 맨 아래로 — scrollIntoView는 임베드 시 호스트 페이지까지 스크롤해
    // 화면이 튀므로(랜딩 데모에서 입력창이 sticky처럼 보이는 문제) 컨테이너 scrollTop만 조정한다.
    const el = messagesRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streamingText])

  const sendMessage = useCallback(async (explicitText?: string) => {
    const userMsg = (explicitText ?? input).trim()
    if (!userMsg || !sessionId || status === 'streaming' || status === 'closed') return

    if (explicitText === undefined) setInput('')
    setMessages(prev => [...prev, { role: 'user', content: userMsg }])
    if (!isHitl) setStatus('streaming')

    try {
      if (await transport.postMessage(sessionId, userMsg) === 'closed') {
        setStatus('closed')
        return
      }
      if (!isHitl) setTypingActor('ai')
    } catch {
      setStatus('error')
    }
  }, [input, sessionId, status, isHitl, transport])

  useImperativeHandle(ref, () => ({ send: (text: string) => { sendMessage(text) } }), [sendMessage])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const blocked = status === 'connecting' || status === 'error' || status === 'closed'

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={styles.headerDot(status)} />
        {brandName ? (
          <div style={styles.headerTitles}>
            <span style={styles.brandTitle}>{brandName}</span>
            <span style={styles.statusSub}>{isHitl ? '상담원 연결 중' : 'AI 상담'}</span>
          </div>
        ) : (
          <span>{isHitl ? '상담원 연결 중' : 'AI 상담'}</span>
        )}
      </div>

      <div style={styles.messages} ref={messagesRef}>
        {messages.map((msg, i) => (
          <div key={i} style={styles.message(msg.role)}>
            {msg.role === 'human_agent' && (
              <div style={styles.agentName}>{msg.agentName}</div>
            )}
            <div data-role={msg.role} style={styles.bubble(msg.role)}>
              {msg.role === 'assistant' ? <Markdown>{msg.content}</Markdown> : msg.content}
            </div>
          </div>
        ))}
        {streamingText && (
          <div style={styles.message('assistant')}>
            <div style={styles.bubble('assistant')}>
              <Markdown>{streamingText}</Markdown>
              <span style={styles.cursor}>▌</span>
            </div>
          </div>
        )}
        {!streamingText && typingActor && (
          <div style={styles.message(typingActor === 'human_agent' ? 'human_agent' : 'assistant')}>
            <div style={{ ...styles.bubble(typingActor === 'human_agent' ? 'human_agent' : 'assistant'), opacity: 0.7 }}>
              <span style={styles.typingDots}>
                {typingActor === 'human_agent' ? '상담원이 입력 중' : 'AI가 응답 중'}
                <span style={styles.dots}>···</span>
              </span>
            </div>
          </div>
        )}
        {status === 'closed' && <div style={styles.closedNotice}>{CLOSED_NOTICE}</div>}
      </div>

      <div style={styles.inputArea}>
        <textarea
          style={styles.textarea}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="메시지를 입력하세요..."
          disabled={blocked}
          rows={2}
        />
        <button
          style={styles.sendBtn(!blocked && !!input.trim())}
          onClick={() => sendMessage()}
          disabled={blocked || !input.trim()}
        >
          전송
        </button>
      </div>
    </div>
  )
})

export default ChatWidget

const styles = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    background: '#fff',
  } as CSSProperties,
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '12px 16px',
    borderBottom: '1px solid #e2e8f0',
    fontWeight: 600,
    fontSize: '15px',
  } as CSSProperties,
  headerDot: (status: Status): CSSProperties => ({
    width: '8px',
    height: '8px',
    borderRadius: '50%',
    background: status === 'ready' ? '#48bb78' : status === 'error' ? '#fc8181' : status === 'closed' ? '#a0aec0' : '#ed8936',
  }),
  headerTitles: {
    display: 'flex',
    flexDirection: 'column',
    lineHeight: 1.2,
  } as CSSProperties,
  brandTitle: {
    fontWeight: 600,
    fontSize: '15px',
  } as CSSProperties,
  statusSub: {
    fontWeight: 400,
    fontSize: '11px',
    color: '#718096',
  } as CSSProperties,
  messages: {
    flex: 1,
    overflowY: 'auto',
    padding: '16px',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  } as CSSProperties,
  message: (role: string): CSSProperties => ({
    display: 'flex',
    flexDirection: 'column',
    alignItems: role === 'user' ? 'flex-end'
      : role === 'system' ? 'center'
        : 'flex-start',
  }),
  agentName: {
    fontSize: 11,
    color: '#718096',
    marginBottom: 2,
    paddingLeft: 4,
  } as CSSProperties,
  bubble: (role: string): CSSProperties => ({
    maxWidth: role === 'system' ? '90%' : '75%',
    padding: role === 'system' ? '6px 12px' : '10px 14px',
    borderRadius: role === 'user' ? '18px 18px 4px 18px'
      : role === 'system' ? '8px'
        : '18px 18px 18px 4px',
    background: role === 'user' ? '#4299e1'
      : role === 'human_agent' ? '#9f7aea'
        : role === 'system' ? '#ecc94b22'
          : '#edf2f7',
    color: role === 'user' ? '#fff'
      : role === 'human_agent' ? '#fff'
        : role === 'system' ? '#744210'
          : '#2d3748',
    fontSize: role === 'system' ? '12px' : '14px',
    lineHeight: '1.5',
    whiteSpace: 'pre-wrap',
    fontStyle: role === 'system' ? 'italic' : 'normal',
  }),
  cursor: {
    animation: 'blink 1s step-end infinite',
    opacity: 0.7,
  } as CSSProperties,
  typingDots: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 13,
  } as CSSProperties,
  dots: {
    animation: 'blink 1.2s step-end infinite',
    letterSpacing: 2,
  } as CSSProperties,
  closedNotice: {
    padding: '12px',
    color: '#4a5568',
    fontSize: '13px',
    textAlign: 'center',
  } as CSSProperties,
  inputArea: {
    display: 'flex',
    gap: '8px',
    padding: '12px 16px',
    borderTop: '1px solid #e2e8f0',
  } as CSSProperties,
  textarea: {
    flex: 1,
    resize: 'none',
    border: '1px solid #e2e8f0',
    borderRadius: '8px',
    padding: '8px 12px',
    fontSize: '14px',
    outline: 'none',
    fontFamily: 'inherit',
  } as CSSProperties,
  sendBtn: (enabled: boolean): CSSProperties => ({
    padding: '0 20px',
    borderRadius: '8px',
    border: 'none',
    background: enabled ? '#4299e1' : '#e2e8f0',
    color: enabled ? '#fff' : '#a0aec0',
    cursor: enabled ? 'pointer' : 'not-allowed',
    fontWeight: 600,
    fontSize: '14px',
    transition: 'all 0.2s',
  }),
}
