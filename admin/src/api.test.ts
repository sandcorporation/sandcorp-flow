import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  startOperatorLogin, tenantAgentLogin, operatorLogout, agentLogout,
  claimEscalation, getSessionCheckpoint,
} from './api'
import { getAccess, setAccess } from './auth'

// facade 고유 동작만 검증한다(생성 CRUD·bearer는 mutator 테스트가 커버).
// 생성 클라이언트 → mutator → fetch 체인을 타므로 fetch는 mutator 계약(text 파싱)으로 mock한다.
function ok(body: unknown, status = 200) {
  return { ok: true, status, headers: new Headers(), text: async () => JSON.stringify(body) }
}
function err(status: number, body: unknown = { detail: 'x' }) {
  return { ok: false, status, headers: new Headers(), text: async () => JSON.stringify(body) }
}

beforeEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('로그인 토큰 부수효과', () => {
  it('startOperatorLogin은 비밀번호 없이 백엔드 /authorize(sandcorp-auth)로 이동한다', () => {
    const assign = vi.fn()
    const original = window.location
    Object.defineProperty(window, 'location', { configurable: true, value: { ...original, assign } })
    try {
      globalThis.fetch = vi.fn()
      startOperatorLogin()
      expect(assign).toHaveBeenCalledWith('/api/auth/authorize')
      expect(globalThis.fetch).not.toHaveBeenCalled()
      expect(getAccess('operator')).toBeNull()
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: original })
    }
  })

  it('tenantAgentLogin이 access(agent)를 저장한다', async () => {
    globalThis.fetch = vi.fn().mockResolvedValueOnce(ok({ access_token: 'ag-acc' }))
    await tenantAgentLogin('Acme', 'alice', 'pw')
    expect(getAccess('agent')).toBe('ag-acc')
  })
})

describe('로그아웃 토큰 부수효과', () => {
  it('operatorLogout이 access를 제거한다', async () => {
    setAccess('operator', 'a')
    globalThis.fetch = vi.fn().mockResolvedValueOnce(ok({ detail: 'logged out' }))
    await operatorLogout()
    expect(getAccess('operator')).toBeNull()
  })

  it('agentLogout이 access를 제거한다', async () => {
    setAccess('agent', 'a')
    globalThis.fetch = vi.fn().mockResolvedValueOnce(ok({ detail: 'logged out' }))
    await agentLogout()
    expect(getAccess('agent')).toBeNull()
  })
})

describe('특수 계약 보존', () => {
  it('claimEscalation은 200이면 ok, 409면 not-ok로 정규화한다', async () => {
    setAccess('agent', 'a')
    globalThis.fetch = vi.fn().mockResolvedValueOnce(ok({ status: 'claimed' }))
    expect(await claimEscalation('e1')).toEqual({ status: 200, ok: true })

    globalThis.fetch = vi.fn().mockResolvedValueOnce(err(409, { detail: 'Already claimed' }))
    expect(await claimEscalation('e2')).toEqual({ status: 409, ok: false })
  })

  it('getSessionCheckpoint은 404면 null을 반환한다', async () => {
    setAccess('agent', 'a')
    globalThis.fetch = vi.fn().mockResolvedValueOnce(err(404, { detail: 'No checkpoint' }))
    expect(await getSessionCheckpoint('s1')).toBeNull()
  })
})
