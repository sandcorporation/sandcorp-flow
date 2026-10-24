import { ReactNode, useState } from 'react'
import { Menu, X } from 'lucide-react'
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
  const [menuOpen, setMenuOpen] = useState(false)  // 좁은 폭에서만 쓰는 메뉴 접힘(햄버거, #90)
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground md:h-screen md:flex-row">
      <aside className="flex flex-shrink-0 flex-col md:overflow-y-auto border-b border-border bg-surface md:w-60 md:border-b-0 md:border-r">
        <div className="flex min-h-12 items-center gap-2 border-b-2 border-ink bg-head px-5 font-cond text-base font-semibold tracking-[0.02em] text-head-foreground">
          <span aria-hidden className="h-4 w-4 flex-none bg-[linear-gradient(180deg,#e7c46a_0_55%,#8a6214_55%_100%)]" />
          <span className="min-w-0 flex-1 truncate">{brand}</span>
          <button type="button" aria-label="메뉴" aria-expanded={menuOpen} onClick={() => setMenuOpen(o => !o)}
            className="flex h-8 w-8 flex-none items-center justify-center border border-head-foreground/40 md:hidden">
            {menuOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
        </div>
        <nav className={cn('flex-col gap-1 px-3 py-2 md:flex md:py-3', menuOpen ? 'flex' : 'hidden')} aria-label="주 메뉴">
          {navItems.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end
              onClick={() => setMenuOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex shrink-0 items-center gap-2 rounded-md border border-transparent border-l-[3px] px-3 py-2 text-sm text-muted-foreground transition-colors hover:border-border hover:bg-raised hover:text-ink',
                  isActive && 'border-border border-l-primary bg-raised font-semibold text-ink',
                )
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col md:min-h-0">
        <header className="flex min-h-12 flex-wrap items-center justify-end gap-2 border-b-2 border-ink bg-head px-4 py-2 text-head-foreground md:px-6">
          {user && <span className="mr-2 truncate font-mono text-xs text-head-foreground/70">{user}</span>}
          {accountHref && (
            <Button asChild variant="head" size="sm">
              <a href={accountHref} target="_blank" rel="noreferrer">내 계정</a>
            </Button>
          )}
          {onSwitch && (
            <Button variant="head" size="sm" onClick={onSwitch}>
              조직 바꾸기
            </Button>
          )}
          <ThemeToggle />
          <Button variant="head" size="sm" onClick={onLogout}>
            로그아웃
          </Button>
          <Button variant="head" size="sm" onClick={onLogoutAll}>
            모든 기기에서 로그아웃
          </Button>
        </header>
        <main className="flex min-h-0 flex-1 flex-col overflow-auto p-4 md:p-6">
          {banner}
          <Outlet />
        </main>
      </div>
    </div>
  )
}
