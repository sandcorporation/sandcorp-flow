import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import FlowMark from './FlowMark'
import ThemeToggle from './ThemeToggle'
import { useEmbed } from '@/lib/embed'

/** 로그인 없는 /try 화면의 겉틀: 머리줄(브랜드 · 오른쪽 자리 · 테마) + 본문. 대시보드 셸과 같은 색·모양. */
export default function TryShell({ right, children, fill = false, title = 'Sandcorp Flow 체험' }: { right?: ReactNode; children: ReactNode; fill?: boolean; title?: string }) {
  const embed = useEmbed()
  if (embed) {
    // 임베드(#180): 브랜드·테마·로그인 머리줄은 숨긴다. 높이는 내용 그대로(뷰포트 단위 없음) — 부모가 iframe 높이를 내용에 맞추므로
    // h-screen/min-h-screen 이면 높이 전달과 순환한다. 오른쪽 자리(남은 대화·가입)만 얇은 줄로 남긴다.
    return (
      <div className="flex flex-col bg-background text-foreground">
        {right && <div className="flex min-h-10 flex-wrap items-center justify-end gap-x-3 gap-y-1 border-b-2 border-ink bg-head px-4 py-1.5 text-head-foreground" data-testid="embed-bar">{right}</div>}
        <main className="flex flex-col p-3 sm:p-4">{children}</main>
      </div>
    )
  }
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground md:h-screen">
      <header className="flex min-h-12 flex-wrap items-center gap-x-3 gap-y-1 border-b-2 border-ink bg-head px-4 py-2 text-head-foreground md:px-6">
        <Link to="/try" className="flex min-w-0 items-center gap-2 font-cond text-base font-semibold tracking-[0.02em]">
          <FlowMark />
          <span className="truncate">{title}</span>
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
