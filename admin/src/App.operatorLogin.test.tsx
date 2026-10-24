import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from './App'

// sandcorp-auth 콜백(#21)이 끝나면 백엔드가 /admin-ui/operator로 돌려보낸다.
// 성공이면 Refresh 쿠키가 심겨 있어 부팅 silent refresh가 access를 받고, 거절이면 ?login_error가 붙어 온다.
function refreshAnswers(operatorOk: boolean) {
  globalThis.fetch = vi.fn(async (url: RequestInfo | URL) => {
    const u = String(url)
    if (u.includes('/api/operator/auth/refresh') && operatorOk) {
      return { ok: true, status: 200, headers: new Headers(), json: async () => ({ access_token: 'op-access' }), text: async () => JSON.stringify({ access_token: 'op-access' }) }
    }
    if (u.includes('/auth/refresh')) {
      return { ok: false, status: 401, headers: new Headers(), json: async () => ({}), text: async () => '{}' }
    }
    return { ok: true, status: 200, headers: new Headers(), json: async () => [], text: async () => '[]' }
  }) as unknown as typeof fetch
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})

describe('App — Operator 로그인 콜백 뒤 (#21)', () => {
  it('콜백이 Refresh 쿠키를 심었으면 부팅 refresh로 Operator 화면에 들어간다', async () => {
    refreshAnswers(true)
    render(<MemoryRouter initialEntries={['/operator']}><App /></MemoryRouter>)

    expect(await screen.findByText('Tenants')).toBeInTheDocument()
    expect(sessionStorage.getItem('op_access')).toBe('op-access')
  })

  it('거절되어 돌아오면 거절 화면을 보여 준다', async () => {
    refreshAnswers(false)
    render(<MemoryRouter initialEntries={['/operator?login_error=member']}><App /></MemoryRouter>)

    expect(await screen.findByText('로그인할 수 없습니다')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '다시 로그인' })).toBeInTheDocument()
  })
})
