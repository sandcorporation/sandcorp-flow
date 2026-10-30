// 랜딩(#141): 루트 소개 → /try 체험 CTA·로그인 링크·문의 링크, 390폭 가로 넘침 없음.
// 위젯 dev 서버는 /embed/landing.html 로 서빙한다(운영 nginx 는 / 로 같은 파일). SHOTS_DIR 이 있으면 사진을 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')

const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const SHOTS = process.env.SHOTS_DIR
const LANDING = `${WIDGET_URL}/embed/landing.html`
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true })
}

test('랜딩: 체험 CTA·템플릿 링크·로그인 링크·문의 링크', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(LANDING)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('흐름으로 만드는 상담 챗봇')
  await shot(page, 'landing-light-1280')

  await expect(page.getByRole('link', { name: '체험해 보기' })).toHaveAttribute('href', '/try')
  await expect(page.getByRole('link', { name: /^운영자 로그인/ })).toHaveAttribute('href', '/admin-ui/')
  await expect(page.getByRole('link', { name: /^테넌트 로그인/ })).toHaveAttribute('href', '/admin-ui/tenant')
  await expect(page.getByRole('link', { name: /도입이 궁금하면 문의/ })).toHaveAttribute('href', 'https://sandcorp.co.kr/#home-inquiry')
  // Made by Sandcorp(#288): 서버 HTML 사전 삽입분은 마운트 때 걷히고 React 푸터의 링크 하나만 남는다.
  const madeBy = page.getByRole('link', { name: 'Made by Sandcorp' })
  await expect(madeBy).toHaveCount(1)
  await expect(madeBy).toHaveAttribute('href', 'https://sandcorp.co.kr/?utm_source=flow&utm_medium=footer')
  await expect(madeBy).not.toHaveAttribute('rel', /.*/)
  await expect(page.locator('main a[href^="mailto:"], main a[href^="tel:"]')).toHaveCount(0)

  // 다크 수동 전환
  await page.getByRole('button', { name: '테마 전환' }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await shot(page, 'landing-dark-1280')
})

test('랜딩의 템플릿 링크가 실제 /try 화면에서 그 템플릿 편집기를 곧바로 연다(#195)', async ({ page }) => {
  await page.goto(LANDING)
  const href = await page.getByRole('link', { name: /^쇼핑몰 고객 지원/ }).getAttribute('href')
  expect(href).toBe('/try?template=shop')
  // 운영에서는 같은 호스트의 nginx 가 /try → /admin-ui/try 로 301 한다(계약: scripts/test-nginx-metrics.sh).
  // e2e 는 nginx 없이 Vite 라 같은 규칙(/admin-ui 접두)을 직접 붙여 연다.
  await page.goto(`${ADMIN_URL}/admin-ui${href}`)
  await expect(page.locator('.react-flow__node').first()).toBeVisible({ timeout: 15000 })
  await expect(page.getByRole('heading', { name: '쇼핑몰 배송·반품 상담' })).toBeVisible()
})

test('랜딩: 운영자 로그인 링크가 admin 의 Operator 로그인 화면으로 이어진다', async ({ page }) => {
  await page.goto(LANDING)
  const op = await page.getByRole('link', { name: /^운영자 로그인/ }).getAttribute('href')
  await page.goto(`${ADMIN_URL}${op}`)
  await expect(page.getByRole('heading', { name: 'Operator 로그인' })).toBeVisible()
})

test('랜딩: 테넌트 로그인 링크가 admin 의 테넌트 로그인 경로로 이어진다', async ({ page }) => {
  await page.goto(LANDING)
  const tenant = await page.getByRole('link', { name: /^테넌트 로그인/ }).getAttribute('href')
  await page.goto(`${ADMIN_URL}${tenant}`)
  await expect(page).toHaveURL(/\/admin-ui\/tenant/)
  await expect(page.locator('body')).not.toContainText('Not Found')
})

for (const [name, url] of [['landing', LANDING], ['terms', `${WIDGET_URL}/embed/terms.html`], ['privacy', `${WIDGET_URL}/embed/privacy.html`]]) {
  test(`${name}: 390폭에서 가로 넘침이 없다`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(url)
    await expect(page.locator('h1')).toBeVisible()
    await shot(page, `${name}-light-390`)
    const over = await page.evaluate(() => ({
      doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      wide: [...document.querySelectorAll('body *')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'visible').map(e => e.tagName),
    }))
    expect(over.doc).toBeLessThanOrEqual(0)
    expect(over.wide).toEqual([])
    // 다크도 같은 폭에서
    await page.emulateMedia({ colorScheme: 'dark' })
    await shot(page, `${name}-dark-390`)
  })
}
