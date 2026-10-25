import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import ThemeToggle from './ThemeToggle'

/** 로그인 없는 /try 화면의 겉틀: 머리줄(브랜드 · 오른쪽 자리 · 테마) + 본문. 대시보드 셸과 같은 색·모양. */
export default function TryShell({ right, children, fill = false }: { right?: ReactNode; children: ReactNode; fill?: boolean }) {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground md:h-screen">
      <header className="flex min-h-12 flex-wrap items-center gap-x-3 gap-y-1 border-b-2 border-ink bg-head px-4 py-2 text-head-foreground md:px-6">
        <Link to="/try" className="flex min-w-0 items-center gap-2 font-cond text-base font-semibold tracking-[0.02em]">
          <span aria-hidden className="h-4 w-4 flex-none bg-[linear-gradient(180deg,#e7c46a_0_55%,#8a6214_55%_100%)]" />
          <span className="truncate">Sandcorp Flow 체험</span>
        </Link>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          {right}
          <ThemeToggle />
        </div>
      </header>
      <main className={`flex min-h-0 flex-1 flex-col p-4 md:p-6 ${fill ? 'overflow-auto' : ''}`}>{children}</main>
    </div>
  )
}
