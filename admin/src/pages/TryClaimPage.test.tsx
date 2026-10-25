import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import * as api from '../api'
import TryClaimPage from './TryClaimPage'

// 「이 흐름으로 가입」(#42) 옮기기 화면: 고객 로그인 뒤(표 ticket) 테넌트를 고르거나 만들고 체험 사본을 옮긴다.
vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return {
    ...real,
    memberTenants: vi.fn(), selectTenant: vi.fn(), createOrganization: vi.fn(), claimTryFlow: vi.fn(),
    trySessionTemplate: vi.fn(), claimLoginUrl: vi.fn(() => '/api/auth/authorize/member?then=try_claim'),
  }
})

const CLAIMED = { tenant_id: 't9', flow_id: 'f9', flow_name: '쇼핑몰 배송·반품 상담' }

function Where() { const l = useLocation(); return <p data-testid="where">{l.pathname}{l.search}</p> }
function renderClaim(onLogin = vi.fn()) {
  render(
    <MemoryRouter initialEntries={['/try/claim']}>
      <Where />
      <Routes>
        <Route path="/try/claim" element={<TryClaimPage onLogin={onLogin} />} />
        <Route path="/tenant/flows/:id" element={<p>편집기</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return onLogin
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.trySessionTemplate).mockResolvedValue('shop_support')
  vi.mocked(api.claimTryFlow).mockResolvedValue(CLAIMED)
})

describe('TryClaimPage — 체험 사본 옮기기 (#42)', () => {
  it('Admin 테넌트가 없으면 이름만 받아 새 테넌트를 만들고 옮긴 뒤 그 편집기로 간다', async () => {
    vi.mocked(api.memberTenants).mockResolvedValue({ name: '김모래', email: null, tenants: [{ tenant_id: 't1', tenant_name: '남의 가게', role: 'member' }] })
    vi.mocked(api.createOrganization).mockResolvedValue({ tenant_id: 't9', tenant_name: '모래 상회', access_token: 'a' })
    const onLogin = renderClaim()

    await userEvent.type(await screen.findByLabelText('조직 이름'), '모래 상회')
    expect(screen.queryByRole('radio')).toBeNull() // 고를 기존 테넌트가 없다(Member 뿐)
    await userEvent.click(screen.getByRole('button', { name: '만들고 흐름 옮기기' }))

    expect(api.createOrganization).toHaveBeenCalledWith('모래 상회', '모래-상회')
    expect(api.claimTryFlow).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('편집기')).toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent('/tenant/flows/f9?claimed=1')
    expect(onLogin).toHaveBeenCalledWith('김모래')
  })

  it('Admin 테넌트가 있으면 「기존 테넌트에 추가」를 골라 그 테넌트로 옮긴다(Member 테넌트는 고를 수 없다)', async () => {
    vi.mocked(api.memberTenants).mockResolvedValue({
      name: null, email: 'me@x.kr',
      tenants: [{ tenant_id: 't1', tenant_name: '내 가게', role: 'admin' }, { tenant_id: 't2', tenant_name: '남의 가게', role: 'member' }],
    })
    vi.mocked(api.selectTenant).mockResolvedValue({ access_token: 'a' })
    renderClaim()

    await userEvent.click(await screen.findByRole('radio', { name: '기존 테넌트에 추가' }))
    expect(screen.getByRole('radio', { name: /내 가게/ })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /남의 가게/ })).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: /내 가게/ }))
    await userEvent.click(screen.getByRole('button', { name: '이 테넌트로 옮기기' }))

    expect(api.selectTenant).toHaveBeenCalledWith('t1')
    expect(api.createOrganization).not.toHaveBeenCalled()
    expect(await screen.findByText('편집기')).toBeInTheDocument()
  })

  it('표가 없으면(로그인 안 됨·만료) 옮기기 로그인으로 보낸다', async () => {
    vi.mocked(api.memberTenants).mockRejectedValue(new Error('401'))
    renderClaim()
    expect(await screen.findByRole('link', { name: 'sandcorp 계정으로 로그인' })).toHaveAttribute('href', '/api/auth/authorize/member?then=try_claim')
  })

  it('체험 세션이 없으면(만료·이미 옮김) 테넌트를 만들기 전에 알리고 체험으로 돌려보낸다', async () => {
    vi.mocked(api.trySessionTemplate).mockResolvedValue(null)
    vi.mocked(api.memberTenants).mockResolvedValue({ name: '김모래', email: null, tenants: [] })
    renderClaim()
    expect(await screen.findByText(/옮길 체험이 없어요/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '템플릿 고르기' })).toHaveAttribute('href', '/try')
    expect(screen.queryByLabelText('조직 이름')).toBeNull()
  })

  it('옮기기가 거절되면 서버 문구를 보인다', async () => {
    vi.mocked(api.memberTenants).mockResolvedValue({ name: null, email: null, tenants: [{ tenant_id: 't1', tenant_name: '내 가게', role: 'admin' }] })
    vi.mocked(api.selectTenant).mockResolvedValue({ access_token: 'a' })
    vi.mocked(api.claimTryFlow).mockRejectedValue(new api.ClaimError('체험 세션이 없거나 만료됐어요.', 'try_no_session'))
    renderClaim()
    await userEvent.click(await screen.findByRole('radio', { name: '기존 테넌트에 추가' }))
    await userEvent.click(screen.getByRole('button', { name: '이 테넌트로 옮기기' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('체험 세션이 없거나 만료됐어요.')
  })
})
