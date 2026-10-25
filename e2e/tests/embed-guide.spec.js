const { test, expect, request } = require('@playwright/test')
const fs = require('fs')
const path = require('path')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const ROUTE = 'tenant/config?section=security'

async function setSlug(token, slug) {
  const api = await request.newContext({ baseURL: API_URL })
  const res = await api.patch('/api/tenant/slug/', { headers: { Authorization: `Bearer ${token}` }, data: { slug } })
  expect(res.ok()).toBeTruthy()
  await api.dispose()
}

// 가로 넘침 — 페이지와 카드 안 코드 블록(자체 가로 스크롤은 허용, 카드 밖으로 밀리면 안 됨).
async function overflow(page) {
  return page.evaluate(() => {
    const de = document.documentElement
    const card = document.querySelector('section[aria-label="내 사이트에 붙이기"]')
    const pre = document.querySelector('[data-testid="embed-code"]')
    return {
      page: de.scrollWidth > de.clientWidth,
      card: !!card && card.scrollWidth > card.clientWidth,
      preOutsideCard: !!card && !!pre && pre.getBoundingClientRect().right > card.getBoundingClientRect().right + 1,
      preScrollsInside: !!pre && getComputedStyle(pre).overflowX === 'auto',
    }
  })
}

test.describe('설정 > 내 사이트에 붙이기 (#166)', () => {
  test('slug 없음 안내 → 저장하면 코드 → 복사한 코드를 빈 HTML에 붙이면 위젯이 뜬다', async ({ page, context }) => {
    test.setTimeout(90_000)
    const tenant = await provisionTenant(`Embed ${Date.now()}`)
    // 시험 스택의 admin 은 http://admin-e2e(비보안 컨텍스트)라 navigator.clipboard 가 없다 — 운영(https)과 달리.
    // 앱이 writeText 에 넘기는 값을 가로채 그대로 검사한다.
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (t) => { window.__copied = t } }, configurable: true })
    })
    await openMemberSession(page, ADMIN_URL, tenant.token, ROUTE)

    // slug 없음: 안내만, 코드·복사 없음
    await expect(page.getByText(/slug를 먼저 정하고 저장/)).toBeVisible()
    await expect(page.getByTestId('embed-code')).toHaveCount(0)

    const slug = `embed-e2e-${Date.now()}`
    await page.getByLabel('Tenant Slug').fill(slug)
    await page.getByRole('button', { name: 'Slug 저장' }).click()
    await expect(page.getByTestId('embed-code')).toContainText(`/chatbot/${slug}/`)

    // 복사 → 앱이 clipboard 에 쓴 값. 비밀 값은 없다.
    await page.getByRole('button', { name: '임베드 코드 복사' }).click()
    const code = await page.evaluate(() => window.__copied)
    expect(code).toMatch(/^<iframe src="[^"]+\/chatbot\/[^"]+\/"/)
    expect(code).toContain('allow="microphone"')
    expect(code).not.toMatch(/token|key|secret/i)

    // 미리보기 링크는 같은 공개 주소
    await expect(page.getByRole('link', { name: /미리보기/ })).toHaveAttribute('href', new RegExp(`/chatbot/${slug}/$`))

    // 붙인 HTML: 시험 스택엔 nginx 가 없어 /chatbot/ 를 Vite 위젯(/embed/chatbot/…)으로 바꿔 넣는다 — 주소 앞부분만 다르고 나머지는 복사한 코드 그대로.
    const embedded = code.replace(/src="[^"]*\/chatbot\//, `src="${WIDGET_URL}/embed/chatbot/`)
    const host = await context.newPage()
    await host.setContent(`<!doctype html><html><body><h1>내 사이트</h1>${embedded}</body></html>`)
    const frame = host.frameLocator('iframe')
    await expect(frame.locator('textarea[placeholder="메시지를 입력하세요..."]')).toBeVisible({ timeout: 15000 })
  })

  for (const theme of ['light', 'dark']) {
    for (const [name, size] of [['390', { width: 390, height: 844 }], ['desktop', { width: 1280, height: 800 }]]) {
      test(`가로 넘침 없음·사진 — ${theme} ${name}`, async ({ page }) => {
        test.setTimeout(60_000)
        await page.setViewportSize(size)
        const tenant = await provisionTenant(`EmbedNarrow ${Date.now()}`)
        await setSlug(tenant.token, `긴-${Date.now()}${Math.random().toString(36).slice(2, 6)}-${'abcdefghij'.repeat(3)}`)
        await page.addInitScript((th) => localStorage.setItem('admin_theme', th), theme)
        await openMemberSession(page, ADMIN_URL, tenant.token, ROUTE)
        await expect(page.getByTestId('embed-code')).toBeVisible()
        const o = await overflow(page)
        expect(o.page).toBe(false)
        expect(o.card).toBe(false)
        expect(o.preOutsideCard).toBe(false)
        expect(o.preScrollsInside).toBe(true)
        if (SHOTS) {
          fs.mkdirSync(SHOTS, { recursive: true })
          await page.locator('section[aria-label="내 사이트에 붙이기"]').scrollIntoViewIfNeeded()
          await page.screenshot({ path: path.join(SHOTS, `embed-${theme}-${name}.png`), fullPage: true })
        }
      })
    }
  }
})
