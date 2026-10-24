import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { MemoryRouter, Routes, Route, Navigate } from 'react-router-dom'
import DashboardLayout, { NavItem } from './DashboardLayout'

const nav: NavItem[] = [
  { to: '/tenant/documents', label: '문서' },
  { to: '/tenant/config', label: '설정' },
]

function renderAt(initial: string) {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route
          path="/tenant"
          element={<DashboardLayout brand="Tenant" navItems={nav} onLogout={() => {}} onLogoutAll={() => {}} />}
        >
          <Route index element={<Navigate to="documents" replace />} />
          <Route path="documents" element={<div>DOCS_SECTION</div>} />
          <Route path="config" element={<div>CONFIG_SECTION</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

describe('DashboardLayout — 셸 + 중첩 라우트', () => {
  it('딥링크가 해당 섹션을 Outlet에 렌더한다', () => {
    renderAt('/tenant/config')
    expect(screen.getByText('CONFIG_SECTION')).toBeInTheDocument()
    expect(screen.queryByText('DOCS_SECTION')).toBeNull()
  })

  it('/tenant index는 documents로 리다이렉트한다', () => {
    renderAt('/tenant')
    expect(screen.getByText('DOCS_SECTION')).toBeInTheDocument()
  })

  it('사이드바 nav 링크(URL)와 로그아웃을 렌더한다', () => {
    renderAt('/tenant/documents')
    expect(screen.getByRole('link', { name: '설정' })).toHaveAttribute('href', '/tenant/config')
    expect(screen.getByRole('link', { name: '문서' })).toHaveAttribute('href', '/tenant/documents')
    expect(screen.getByRole('button', { name: '로그아웃' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '테마 전환' })).toBeInTheDocument()
  })

  it('내 계정은 sandcorp-auth 계정 화면으로, 조직 바꾸기는 콜백으로 이어진다', async () => {
    const onSwitch = vi.fn()
    render(
      <MemoryRouter initialEntries={['/tenant/documents']}>
        <Routes>
          <Route path="/tenant" element={<DashboardLayout brand="Alpha" navItems={nav} user="김고객" accountHref="https://auth.sandcorp.co.kr/account" onSwitch={onSwitch} onLogout={() => {}} onLogoutAll={() => {}} />}>
            <Route path="documents" element={<div>DOCS_SECTION</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('김고객')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '내 계정' })).toHaveAttribute('href', 'https://auth.sandcorp.co.kr/account')
    await userEvent.click(screen.getByRole('button', { name: '조직 바꾸기' }))
    expect(onSwitch).toHaveBeenCalled()
  })
})
