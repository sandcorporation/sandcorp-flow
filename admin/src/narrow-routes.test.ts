import { existsSync, readFileSync } from 'node:fs'
import { describe, it, expect } from 'vitest'

// App.tsx 의 라우트가 390폭 넘침 e2e(e2e/tests/narrow-overflow.spec.js)의 목록에 모두 있는지 지킨다(#90).
// 새 화면을 만들고 e2e/tests/narrow-routes.json 에 더하지 않으면 여기서 빨개진다.
describe('390폭 넘침 시험의 라우트 목록', () => {
  it('App.tsx 의 모든 라우트가 narrow-routes.json 에 있다', () => {
    const app = readFileSync('src/App.tsx', 'utf8')
    const listed = JSON.parse(readFileSync(existsSync('/e2e/narrow-routes.json') ? '/e2e/narrow-routes.json' : '../e2e/tests/narrow-routes.json', 'utf8')) as Record<string, string[] | string>
    const found: string[] = []
    let role = ''
    for (const m of app.matchAll(/<Route\s+path="([^"]+)"/g)) {
      const p = m[1]
      if (p === '/operator' || p === '/tenant') { role = p.slice(1); continue }
      if (p.startsWith('/try') || p.startsWith('/flows/')) { found.push(`public/${p.slice(1)}`); continue } // 로그인 없는 공개 화면(#37·#39)
      if (role) found.push(`${role}/${p}`)
    }
    const want = Object.entries(listed).filter(([k]) => !k.startsWith('_')).flatMap(([r, l]) => (l as string[]).map(p => `${r}/${p}`))
    expect(found.length).toBeGreaterThan(0)
    expect([...found].sort()).toEqual([...want].sort())
  })
})
