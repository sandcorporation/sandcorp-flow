import { ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import ThemeToggle from './ThemeToggle'

export interface NavItem {
  to: string
  label: string
  icon?: ReactNode
}

// 공유 대시보드 셸(ADR-0017): 좌측 사이드바 + 상단 바 + <Outlet/>. Operator/Tenant가 nav만 달리해 공유.
export default function DashboardLayout({
  brand,
  navItems,
  user,
  accountHref,
  onSwitch,
  onLogout,
  onLogoutAll,
  banner,
}: {
  brand: string
  navItems: NavItem[]
  user?: string | null
  accountHref?: string  // sandcorp-auth 계정 화면(이름·이메일은 거기서 바꾼다)
  onSwitch?: () => void  // 다른 조직으로 들어가기
  onLogout: () => void
  onLogoutAll: () => void
  banner?: ReactNode  // 본문 위 안내(체험 한도 등, #41)
}) {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground md:flex-row">
      <aside className="flex flex-shrink-0 flex-col border-b border-border bg-card md:w-60 md:border-b-0 md:border-r">
        <div className="truncate px-5 py-3 text-base font-semibold md:py-4">{brand}</div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-col md:pb-0" aria-label="주 메뉴">
          {navItems.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end
              className={({ isActive }) =>
                cn(
                  'flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground',
                  isActive && 'bg-accent font-medium text-accent-foreground',
                )
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-14 flex-wrap items-center justify-end gap-2 border-b border-border px-4 py-2 md:px-6">
          {user && <span className="mr-2 truncate text-sm text-muted-foreground">{user}</span>}
          {accountHref && (
            <Button asChild variant="ghost" size="sm">
              <a href={accountHref} target="_blank" rel="noreferrer">내 계정</a>
            </Button>
          )}
          {onSwitch && (
            <Button variant="ghost" size="sm" onClick={onSwitch}>
              조직 바꾸기
            </Button>
          )}
          <ThemeToggle />
          <Button variant="outline" size="sm" onClick={onLogout}>
            로그아웃
          </Button>
          <Button variant="ghost" size="sm" onClick={onLogoutAll}>
            모든 기기에서 로그아웃
          </Button>
        </header>
        <main className="flex-1 overflow-auto p-4 md:p-6">
          {banner}
          <Outlet />
        </main>
      </div>
    </div>
  )
}
