import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import * as api from '../api'
import TenantLogin from './TenantLogin'

vi.mock('../api')

function at(search: string) {
  window.history.replaceState(null, '', `/admin-ui/tenant${search}`)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.memberLoginUrl).mockReturnValue('/api/auth/authorize')
})
afterEach(() => at(''))

describe('TenantLogin — sandcorp-auth 고객 로그인 (#40)', () => {
  it('비밀번호 폼 없이 sandcorp-auth 로그인으로 보낸다', () => {
    at('')
    render(<TenantLogin onLogin={vi.fn()} />)

    expect(screen.getByRole('link', { name: 'sandcorp 계정으로 로그인' })).toHaveAttribute('href', '/api/auth/authorize')
    expect(screen.queryByPlaceholderText('비밀번호')).toBeNull()
    expect(screen.queryByText('새 조직 만들기')).toBeNull()
  })

  it('만료된 초대 링크로 돌아오면 안내한다', () => {
    at('?login_error=invite_expired')
    render(<TenantLogin onLogin={vi.fn()} />)
    expect(screen.getByText('초대 링크가 만료되었습니다. 관리자에게 새 링크를 받으세요.')).toBeInTheDocument()
  })

  it('속한 조직이 없으면 초대가 필요하다고 안내한다', () => {
    at('?onboarding=1')
    render(<TenantLogin onLogin={vi.fn()} />)
    expect(screen.getByText('아직 속한 조직이 없습니다. 관리자에게 초대 링크를 받으세요.')).toBeInTheDocument()
  })

  it('조직이 여럿이면 골라서 들어간다', async () => {
    at('?select_tenant=1')
    vi.mocked(api.memberTenants).mockResolvedValue({
      name: '김고객', email: null,
      tenants: [
        { tenant_id: 't1', tenant_name: 'Alpha', role: 'admin' },
        { tenant_id: 't2', tenant_name: 'Beta', role: 'member' },
      ],
    })
    vi.mocked(api.selectTenant).mockResolvedValue({ access_token: 't' })
    const onLogin = vi.fn()
    render(<TenantLogin onLogin={onLogin} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Beta (Member)' }))

    expect(api.selectTenant).toHaveBeenCalledWith('t2')
    expect(onLogin).toHaveBeenCalledWith('김고객')
  })
})
