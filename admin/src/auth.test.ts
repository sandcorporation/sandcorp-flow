import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { authFetch, refresh, bootSilentRefresh, getAccess, setAccess, clearAccess } from './auth'

beforeEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('authFetch — 401 투명 refresh + 재시도', () => {
  it('401이면 refresh로 새 access를 받아 원요청을 재시도한다', async () => {
    setAccess('operator', 'old-access')
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ status: 401, ok: false })                                  // 원요청 401
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ access_token: 'new-access' }) }) // refresh
      .mockResolvedValueOnce({ status: 200, ok: true })                                    // 재시도
    globalThis.fetch = fetchMock

    const res = await authFetch('operator', '/api/operator/tenants/')

    expect(res.status).toBe(200)
    // 재시도 요청은 새 access를 달고 나간다
    const retryInit = fetchMock.mock.calls[2][1]
    expect(retryInit.headers.Authorization).toBe('Bearer new-access')
    expect(getAccess('operator')).toBe('new-access')
  })
})

describe('bootSilentRefresh', () => {
  it('access가 없으면 refresh를 호출해 복구한다', async () => {
    clearAccess('operator')
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true, status: 200, json: async () => ({ access_token: 'boot-access' }),
    })
    globalThis.fetch = fetchMock

    await bootSilentRefresh('operator')

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/operator/auth/refresh'),
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    )
    expect(getAccess('operator')).toBe('boot-access')
  })
})

describe('bootSilentRefresh — 로그인 없는 체험·공개 보기(#150)', () => {
  const at = (path: string) => window.history.pushState({}, '', path)
  afterEach(() => at('/'))
  it.each(['/admin-ui/try', '/admin-ui/try/flow/abc', '/admin-ui/try/view/xyz', '/admin-ui/flows/some-slug'])('%s 에서는 refresh 를 시도하지 않는다', async (path) => {
    at(path)
    clearAccess('operator'); clearAccess('agent')
    const fetchMock = vi.fn()
    globalThis.fetch = fetchMock
    expect(await bootSilentRefresh('operator')).toBe(false)
    expect(await bootSilentRefresh('agent')).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it('/try/claim(로그인 뒤 착지)은 refresh 쿠키로 access 를 복구한다', async () => {
    at('/admin-ui/try/claim')
    clearAccess('agent')
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ access_token: 'a' }) })
    globalThis.fetch = fetchMock
    expect(await bootSilentRefresh('agent')).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('refresh — single-flight (동시 401 경쟁 방지)', () => {
  it('동시 refresh 호출은 refresh 엔드포인트를 한 번만 치고 결과를 공유한다', async () => {
    clearAccess('agent')
    let calls = 0
    let release!: () => void
    const gate = new Promise<void>((resolve) => { release = resolve }) // 회전 지연 — 동시 호출이 in-flight를 공유해야 하니 응답을 붙잡아 둔다
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      calls++
      await gate
      return { ok: true, status: 200, json: async () => ({ access_token: 'shared-access' }) }
    })

    const all = Promise.all([refresh('agent'), refresh('agent'), refresh('agent')])
    release()
    const [a, b, c] = await all

    expect(a && b && c).toBe(true)
    // single-flight 없으면 같은 refresh 쿠키로 3번 회전 → 서버 reuse 탐지가 family를 폐기(로그아웃).
    expect(calls).toBe(1)
    expect(getAccess('agent')).toBe('shared-access')
  })
})

describe('refresh 실패', () => {
  it('refresh가 401이면 access를 제거하고 false를 반환한다', async () => {
    setAccess('operator', 'stale')
    globalThis.fetch = vi.fn().mockResolvedValueOnce({ ok: false, status: 401 })

    const ok = await refresh('operator')

    expect(ok).toBe(false)
    expect(getAccess('operator')).toBeNull()
  })
})
