// /try 임베드 모드(#180): 실제 스택에서 머리줄 숨김·넘침 없음·고정 높이(순환 없음)·hello→height.
// iframe 호스트(두 호스트)는 #181 몫이라 여기선 최상위 문서로 연다: 최상위에선 window.parent === window 라 자기 자신이 부모 역할을 한다.
const fs = require('fs')
const path = require('path')
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
  await page.setViewportSize({ width: 929, height: 700 })
  await page.goto(`${ADMIN_URL}/admin-ui/try?embed=1&template=home`)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await expect(page.getByRole('banner')).toHaveCount(0)
  // 세션 없는 동안 가입 링크는 새 탭 중간 페이지(#198). 화면에서 첫 저장(노드 이름 바꾸기)으로 세션을 만든 뒤 다시 연다.
  const pre = page.getByRole('link', { name: '이 흐름으로 가입' })
  await expect(pre).toHaveAttribute('href', '/admin-ui/try/signup?template=home')
  await expect(pre).toHaveAttribute('target', '_blank')
  await page.locator('[data-node-id="general"]').click()
  await page.locator('aside[aria-label="노드 설정"]').getByLabel('이름').fill('임베드에서 고친 이름')
  await page.locator('aside[aria-label="노드 설정"]').getByLabel('이름').blur() // 이름은 포커스를 잃을 때 저장된다
  await expect.poll(() => page.evaluate(async () => (await (await fetch('/api/try/session/')).json())?.template)).toBe('sandcorp_home')
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
  await page.setViewportSize({ width: 929, height: 1100 })
  await page.waitForTimeout(300)
  expect(await rootH()).toBe(h700)

  // hello 전에는 아무것도 안 보내고, hello 뒤에는 그 origin 으로 높이를 보낸다
  await page.evaluate(() => { window.__got = []; window.addEventListener('message', (e) => { if (e.data?.type === 'sandcorp-flow:height') window.__got.push(e.data.height) }) })
  await page.waitForTimeout(300)
  expect(await page.evaluate(() => window.__got.length)).toBe(0)
  await page.evaluate(() => window.postMessage({ type: 'sandcorp-flow:hello' }, location.origin))
  await expect.poll(() => page.evaluate(() => window.__got[0])).toBeGreaterThan(h700 - 2) // 소수 높이: 반올림과 올림이 1px 다를 수 있다
  expect(await page.evaluate(() => window.__got[0])).toBeLessThan(h700 + 2)
})

// 임베드 첫 화면(#199): iframe 위쪽 640px 안에 캔버스와 노드가 보인다. 929px 는 격자가 한 줄로 쌓이는 폭(lg 미만) —
// 노드 목록 8칸이 캔버스를 아래로 밀던 폭(대장 운영 관찰)이라 캔버스가 먼저 와야 한다.
const SHOTS = process.env.SHOTS_DIR
for (const width of [929, 1200, 390]) {
  test(`임베드 첫 화면 @${width}: 위쪽 640px 안에 캔버스와 노드가 보인다`, async ({ page }) => {
    await page.setViewportSize({ width, height: 640 })
    await page.goto(`${ADMIN_URL}/admin-ui/try?embed=1&template=home`)
    const canvas = page.getByTestId('flow-canvas')
    await expect(page.locator('.react-flow__node').first()).toBeVisible()
    const top = await canvas.evaluate((el) => Math.round(el.getBoundingClientRect().top + window.scrollY))
    expect(top).toBeLessThan(200) // 노드 목록이 위에 쌓여 캔버스를 밀어내지 않는다
    const nodes = await page.locator('.react-flow__node').evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { top: r.top + window.scrollY, bottom: r.bottom + window.scrollY, h: r.height } }))
    expect(nodes.some((n) => n.h > 0 && n.top >= 0 && n.bottom <= 640)).toBe(true) // 노드 하나가 첫 화면 안에 통째로 있다
    if (SHOTS) {
      fs.mkdirSync(SHOTS, { recursive: true })
      await page.screenshot({ path: path.join(SHOTS, `embed-first-${width}.png`) })
    }
  })
}
