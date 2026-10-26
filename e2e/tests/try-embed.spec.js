// /try 임베드 모드(#180): 실제 스택에서 머리줄 숨김·넘침 없음·고정 높이(순환 없음)·hello→height.
// iframe 호스트(두 호스트)는 #181 몫이라 여기선 최상위 문서로 연다: 최상위에선 window.parent === window 라 자기 자신이 부모 역할을 한다.
const { test, expect } = require('@playwright/test')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const noOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)

for (const width of [390, 768, 1200]) {
  test(`임베드 /try?embed=1&template=home @${width}: 머리줄 없음·곧바로 홈 편집기·가로 넘침 없음`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 })
    await page.goto(`${ADMIN_URL}/admin-ui/try?embed=1&template=home`)
    await expect(page.locator('.react-flow__node').first()).toBeVisible() // 방문 즉시 편집기(#195) — 버튼 없음
    await expect(page.getByRole('button', { name: /고쳐 보기/ })).toHaveCount(0)
    await expect(page.getByRole('banner')).toHaveCount(0)
    await expect(page.getByText('Sandcorp Flow 체험')).toHaveCount(0)
    expect(await noOverflow(page)).toBe(true)
  })
}

test('임베드 편집기: SPA 이동·새로고침에도 유지, 가입 링크는 새 탭, 높이는 뷰포트와 무관, hello→height', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 390, height: 700 })
  await page.goto(`${ADMIN_URL}/admin-ui/try?embed=1&template=home`)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await expect(page.getByRole('banner')).toHaveCount(0)
  // 세션 없는 동안 가입 링크는 새 탭 중간 페이지(#198). 다른 탭의 첫 쓰기로 세션이 생긴 셈 치고 다시 연다.
  const pre = page.getByRole('link', { name: '이 흐름으로 가입' })
  await expect(pre).toHaveAttribute('href', '/admin-ui/try/signup?template=home')
  await expect(pre).toHaveAttribute('target', '_blank')
  await page.evaluate(async () => { await fetch('/api/try/sessions/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ template: 'sandcorp_home' }) }) })
  await page.reload()
  await expect(page).toHaveURL(/\/try\?embed=1&template=home$/)
  const signup = page.getByRole('link', { name: '이 흐름으로 가입' })
  await expect(signup).toHaveAttribute('target', '_blank')
  await expect(signup).toHaveAttribute('rel', /noopener/)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await page.reload()
  await expect(page.getByRole('banner')).toHaveCount(0)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  expect(await noOverflow(page)).toBe(true)

  // 순환 없음: 뷰포트 높이를 바꿔도 내용(#root) 높이는 같다
  const rootH = () => page.evaluate(() => Math.round(document.getElementById('root').getBoundingClientRect().height))
  const h700 = await rootH()
  await page.setViewportSize({ width: 390, height: 1100 })
  await page.waitForTimeout(300)
  expect(await rootH()).toBe(h700)

  // hello 전에는 아무것도 안 보내고, hello 뒤에는 그 origin 으로 높이를 보낸다
  await page.evaluate(() => { window.__got = []; window.addEventListener('message', (e) => { if (e.data?.type === 'sandcorp-flow:height') window.__got.push(e.data.height) }) })
  await page.waitForTimeout(300)
  expect(await page.evaluate(() => window.__got.length)).toBe(0)
  await page.evaluate(() => window.postMessage({ type: 'sandcorp-flow:hello' }, location.origin))
  await expect.poll(() => page.evaluate(() => window.__got[0])).toBe(h700)
})
