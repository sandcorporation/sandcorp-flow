import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { detectEmbed, installEmbedBridge, resetEmbedForTest } from './embed'

beforeEach(() => resetEmbedForTest())

describe('임베드 판정·유지(#180)', () => {
  it('?embed=1 이면 임베드이고, 쿼리가 사라진 뒤(SPA 이동·새로고침)에도 유지한다', () => {
    expect(detectEmbed('')).toBe(false)
    expect(detectEmbed('?embed=1&template=home')).toBe(true)
    expect(detectEmbed('')).toBe(true)
    expect(sessionStorage.getItem('sandcorp-flow:embed')).toBe('1')
  })
  it('메모리를 잃어도(새로고침) sessionStorage 로 이어진다', () => {
    sessionStorage.setItem('sandcorp-flow:embed', '1')
    expect(detectEmbed('')).toBe(true)
  })
  it('embed 가 1 이 아니면 임베드가 아니다', () => {
    expect(detectEmbed('?embed=0')).toBe(false)
    expect(detectEmbed('?template=home')).toBe(false)
  })
})

describe('높이 전달 규약 — hello → height(#180)', () => {
  let stop: () => void
  let post: ReturnType<typeof vi.spyOn>
  let roCallback: () => void = () => {}
  let height = 640
  const root = document.createElement('div')
  root.id = 'root'

  beforeEach(() => {
    roCallback = () => {}
    height = 640
    root.getBoundingClientRect = () => ({ height }) as DOMRect
    document.body.appendChild(root)
    vi.stubGlobal('ResizeObserver', class { constructor(cb: () => void) { roCallback = cb } observe() {} disconnect() {} })
    post = vi.spyOn(window, 'postMessage').mockImplementation(() => {})
    detectEmbed('?embed=1')
    stop = installEmbedBridge()
  })
  afterEach(() => { stop(); root.remove(); vi.unstubAllGlobals(); post.mockRestore() })

  const hello = (init: { source?: Window | null; origin?: string; data?: unknown }) =>
    window.dispatchEvent(new MessageEvent('message', { data: init.data ?? { type: 'sandcorp-flow:hello' }, origin: init.origin ?? 'https://portfolio.example', source: init.source === undefined ? window.parent : init.source }))

  it('hello 가 오기 전에는 아무것도 보내지 않는다', () => {
    roCallback()
    expect(post).not.toHaveBeenCalled()
  })
  it('부모가 hello 를 보내면 그 origin 으로만 높이를 보낸다(와일드카드 아님)', () => {
    hello({})
    expect(post).toHaveBeenCalledWith({ type: 'sandcorp-flow:height', height: 640 }, 'https://portfolio.example')
    height = 900
    roCallback()
    expect(post).toHaveBeenLastCalledWith({ type: 'sandcorp-flow:height', height: 900 }, 'https://portfolio.example')
    expect(post.mock.calls.every((c) => c[1] !== '*')).toBe(true)
  })
  it('부모가 아닌 창이 보낸 hello 는 무시한다', () => {
    const other = document.createElement('iframe')
    document.body.appendChild(other)
    hello({ source: other.contentWindow })
    roCallback()
    expect(post).not.toHaveBeenCalled()
    other.remove()
  })
  it('type 이 다르거나 origin 이 "null" 이면 무시한다', () => {
    hello({ data: { type: 'other' } })
    hello({ origin: 'null' })
    hello({ data: 'sandcorp-flow:hello' })
    roCallback()
    expect(post).not.toHaveBeenCalled()
  })
  it('임베드가 아니면 hello 에도 답하지 않는다', () => {
    stop(); resetEmbedForTest(); stop = installEmbedBridge()
    hello({})
    expect(post).not.toHaveBeenCalled()
  })
})
