import { useEffect, useState } from 'react'

// 라이트·다크 수동 전환. admin(ThemeToggle)과 같은 저장 키·`.dark` 클래스를 쓴다(같은 호스트라 기억이 이어진다).
// 저장값이 없으면 prefers-color-scheme 을 따른다(CSS). `.light` 는 시스템이 다크일 때 라이트를 고른 표시.
const KEY = 'admin_theme'

function read(): string | null {
  try { return localStorage.getItem(KEY) } catch { return null }
}

function systemDark(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches
}

export default function ThemeToggle() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))

  useEffect(() => {
    const saved = read()
    const el = document.documentElement
    if (saved === 'dark') { el.classList.add('dark'); setDark(true) }
    else if (saved === 'light') { el.classList.add('light'); setDark(false) }
    else setDark(systemDark())
  }, [])

  const toggle = () => {
    const next = !dark
    setDark(next)
    const el = document.documentElement
    el.classList.toggle('dark', next)
    el.classList.toggle('light', !next)
    try { localStorage.setItem(KEY, next ? 'dark' : 'light') } catch { /* 저장 불가여도 화면은 바뀐다 */ }
  }

  return (
    <button type="button" className="theme-btn" aria-label="테마 전환" aria-pressed={dark} onClick={toggle}>
      {dark ? '라이트' : '다크'}
    </button>
  )
}
