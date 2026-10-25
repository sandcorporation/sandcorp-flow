import { describe, it, expect, beforeEach, vi } from 'vitest'
import { customInstance } from './mutator'
import { setAccess, getAccess } from './auth'

beforeEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

// orval fetch 클라이언트 계약: customInstance<T>(url, init) → { data, status, headers }
type Wrapped<T> = { data: T; status: number; headers: Headers }

describe('customInstance — kind 판정 + bearer + credentials', () => {
  it('operator URL은 operator access를 Bearer로 싣고 파싱된 데이터를 래핑해 반환한다', async () => {
    setAccess('operator', 'op-tok')
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true, status: 200, headers: new Headers(), text: async () => JSON.stringify({ ok: 1 }),
    })
    globalThis.fetch = fetchMock

    const res = await customInstance<Wrapped<{ ok: number }>>('/api/operator/tenants/', { method: 'GET' })

    expect(res.data).toEqual({ ok: 1 })
    expect(res.status).toBe(200)
    const init = fetchMock.mock.calls[0][1]
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer op-tok')
    expect(init.credentials).toBe('include')
  })

  it('agent URL은 agent access를 싣는다', async () => {
    setAccess('agent', 'ag-tok')
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true, status: 200, headers: new Headers(), text: async () => '{}',
    })
    globalThis.fetch = fetchMock

    await customInstance('/api/tenant/config/', { method: 'GET' })

    const init = fetchMock.mock.calls[0][1]
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer ag-tok')
  })
})

describe('customInstance — 401 투명 refresh', () => {
  it('401이면 refresh 후 새 토큰으로 재시도하고 데이터를 반환한다', async () => {
    setAccess('agent', 'old')
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 401, headers: new Headers(), text: async () => '' })                  // 원요청 401
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ access_token: 'new' }) })                    // refresh
      .mockResolvedValueOnce({ ok: true, status: 200, headers: new Headers(), text: async () => JSON.stringify({ v: 2 }) }) // 재시도
    globalThis.fetch = fetchMock

    const res = await customInstance<Wrapped<{ v: number }>>('/api/tenant/config/', { method: 'GET' })

    expect(res.data).toEqual({ v: 2 })
    expect(getAccess('agent')).toBe('new')
    expect(new Headers(fetchMock.mock.calls[2][1].headers).get('Authorization')).toBe('Bearer new')
  })

  it('refresh가 실패하면 throw한다', async () => {
    setAccess('agent', 'x')
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 401, headers: new Headers(), text: async () => '' })  // 원요청 401
      .mockResolvedValueOnce({ ok: false, status: 401 })                                                 // refresh 실패
    await expect(customInstance('/api/tenant/config/', { method: 'GET' })).rejects.toThrow()
  })
})

describe('customInstance — 비-2xx throw', () => {
  it('재시도 후에도 비-2xx면 status를 담아 throw한다', async () => {
    setAccess('agent', 'x')
    globalThis.fetch = vi.fn().mockResolvedValueOnce({
      ok: false, status: 500, headers: new Headers(), text: async () => 'boom',
    })
    await expect(customInstance('/api/tenant/config/', { method: 'GET' }))
      .rejects.toMatchObject({ status: 500 })
  })
})

describe('customInstance — 공개 체험 API(/api/try, #37)', () => {
  it('Authorization 을 싣지 않고 쿠키(credentials)만 쓴다 — 로그인 중이어도', async () => {
    setAccess('agent', 'ag-tok')
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, headers: new Headers(), text: async () => '{}' })
    globalThis.fetch = fetchMock

    await customInstance('/api/try/session/', { method: 'GET' })

    const init = fetchMock.mock.calls[0][1]
    expect(new Headers(init.headers).get('Authorization')).toBeNull()
    expect(init.credentials).toBe('include')
  })

  it('401 은 refresh 를 시도하지 않고 try_no_session 이면 세션 끊김을 알린다', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 401, headers: new Headers(), text: async () => JSON.stringify({ detail: '없음', code: 'try_no_session' }),
    })
    globalThis.fetch = fetchMock
    const lost = vi.fn()
    window.addEventListener('flow:try-session-lost', lost)

    await expect(customInstance('/api/try/flows/f1/', { method: 'GET' })).rejects.toMatchObject({ status: 401 })

    window.removeEventListener('flow:try-session-lost', lost)
    expect(fetchMock).toHaveBeenCalledTimes(1) // /auth/refresh 호출 없음, 재시도 없음
    expect(lost).toHaveBeenCalledTimes(1)
  })
})
