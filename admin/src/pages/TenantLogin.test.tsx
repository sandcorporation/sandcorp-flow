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
  vi.mocked(api.memberLoginUrl).mockReturnValue('/api/auth/authorize/member')
})
afterEach(() => at(''))

describe('TenantLogin — sandcorp-auth 고객 로그인 (#40)', () => {
  it('비밀번호 폼 없이 sandcorp-auth 로그인으로 보낸다', () => {
    at('')
    render(<TenantLogin onLogin={vi.fn()} />)

    expect(screen.getByRole('link', { name: 'sandcorp 계정으로 로그인' })).toHaveAttribute('href', '/api/auth/authorize/member')
    expect(screen.queryByPlaceholderText('비밀번호')).toBeNull()
    expect(screen.queryByText('새 조직 만들기')).toBeNull()
  })

  it('만료된 초대 링크로 돌아오면 안내한다', () => {
    at('?login_error=invite_expired')
    render(<TenantLogin onLogin={vi.fn()} />)
    expect(screen.getByText('초대 링크가 만료되었습니다. 관리자에게 새 링크를 받으세요.')).toBeInTheDocument()
  })

  it('속한 조직이 없으면 조직 만들기 폼을 보여 준다(초대 안내도 함께)', async () => {
    at('?onboarding=1')
    vi.mocked(api.memberTenants).mockResolvedValue({ name: '김모래', email: null, tenants: [] })
    render(<TenantLogin onLogin={vi.fn()} />)
    expect(await screen.findByText(/김모래님, 아직 속한 조직이 없습니다/)).toBeInTheDocument()
    expect(screen.getByText(/관리자에게 초대 링크를 받아 합류하세요/)).toBeInTheDocument()
    expect(screen.getByLabelText('조직 이름')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '내 계정' })).toHaveAttribute('href', 'https://auth.sandcorp.co.kr/account')
  })

  it('조직 이름을 넣으면 공개 주소를 제안하고, 만들면 그 사람으로 세션을 연다', async () => {
    at('?onboarding=1')
    vi.mocked(api.memberTenants).mockResolvedValue({ name: '김모래', email: null, tenants: [] })
    vi.mocked(api.createOrganization).mockResolvedValue({ tenant_id: 't1', tenant_name: '모래 상회', access_token: 'a' })
    const onLogin = vi.fn()
    render(<TenantLogin onLogin={onLogin} />)
    await screen.findByText(/김모래님/)

    await userEvent.type(screen.getByLabelText('조직 이름'), ' 모래 상회! ')
    expect(screen.getByLabelText('공개 주소')).toHaveValue('모래-상회')
    await userEvent.click(screen.getByRole('button', { name: '조직 만들기' }))

    expect(api.createOrganization).toHaveBeenCalledWith('모래 상회!', '모래-상회')
    expect(onLogin).toHaveBeenCalledWith('김모래')
  })

  it('직접 고친 공개 주소는 이름을 바꿔도 덮어쓰지 않는다', async () => {
    at('?onboarding=1')
    vi.mocked(api.memberTenants).mockResolvedValue({ name: null, email: 'a@b.c', tenants: [] })
    render(<TenantLogin onLogin={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('공개 주소'), 'my-shop')
    await userEvent.type(screen.getByLabelText('조직 이름'), '가게')
    expect(screen.getByLabelText('공개 주소')).toHaveValue('my-shop')
  })

  it('중복·형식 거절이면 서버 문구를 보여 주고 머문다', async () => {
    at('?onboarding=1')
    vi.mocked(api.memberTenants).mockResolvedValue({ name: null, email: null, tenants: [] })
    vi.mocked(api.createOrganization).mockRejectedValue(Object.assign(new Error('이미 쓰는 주소예요.'), { reason: 'slug_taken' }))
    const onLogin = vi.fn()
    render(<TenantLogin onLogin={onLogin} />)
    await userEvent.type(screen.getByLabelText('조직 이름'), '가게')
    await userEvent.click(screen.getByRole('button', { name: '조직 만들기' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('이미 쓰는 주소예요.')
    expect(onLogin).not.toHaveBeenCalled()
  })

  it('온보딩 표가 만료되면 다시 로그인하게 한다', async () => {
    at('?onboarding=1')
    vi.mocked(api.memberTenants).mockRejectedValue(new Error('401'))
    render(<TenantLogin onLogin={vi.fn()} />)
    expect(await screen.findByText('로그인이 만료되었습니다. 다시 로그인하세요.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'sandcorp 계정으로 로그인' })).toHaveAttribute('href', '/api/auth/authorize/member')
  })

  it.each([
    ['owner', /대표 계정은 고객 화면에 들어올 수 없습니다/],
    ['invite_used', /이미 사용된 초대 링크/],
    ['invite_invalid', /유효하지 않은 초대 링크/],
    ['denied', /취소했습니다/],
    ['nonsense', /로그인하지 못했습니다/],
  ])('로그인 실패 사유 %s는 사유별 문구와 다시 로그인 버튼을 보여 준다', (reason, text) => {
    at(`?login_error=${reason}`)
    render(<TenantLogin onLogin={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent(text)
    expect(screen.getByRole('link', { name: '다시 로그인' })).toHaveAttribute('href', '/api/auth/authorize/member')
  })

  it('조직 선택 중 티켓이 만료되면 다시 로그인하게 한다', async () => {
    at('?select_tenant=1')
    vi.mocked(api.memberTenants).mockRejectedValue(new Error('401'))
    render(<TenantLogin onLogin={vi.fn()} />)
    expect(await screen.findByText('로그인이 만료되었습니다. 다시 로그인하세요.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'sandcorp 계정으로 로그인' })).toBeInTheDocument()
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

    expect(await screen.findByText(/김고객님, 들어갈 조직을 고르세요/)).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: /Beta/ }))

    expect(api.selectTenant).toHaveBeenCalledWith('t2')
    expect(onLogin).toHaveBeenCalledWith('김고객')
  })
})
