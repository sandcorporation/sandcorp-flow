// Flow 아이콘(#142): 엔트리마다 <link rel="icon"> 이 있고 그 href 가 200, manifest·apple-touch-icon 200,
// 로고가 보인다. 운영 nginx 의 루트 /favicon.ico 는 e2e(vite dev)에 없어 dist 파일(/embed/favicon.ico)로 대신 본다.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')

const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const SHOTS = process.env.SHOTS_DIR
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`) })
}

const ENTRIES = [
  ['랜딩', `${WIDGET_URL}/embed/landing.html`],
  ['약관', `${WIDGET_URL}/embed/terms.html`],
  ['처리방침', `${WIDGET_URL}/embed/privacy.html`],
  ['위젯 챗봇', `${WIDGET_URL}/embed/`],
  ['admin', `${ADMIN_URL}/admin-ui/`],
]

for (const [name, url] of ENTRIES) {
  test(`${name}: favicon·apple-touch-icon·manifest 링크가 있고 모두 200`, async ({ page, request }) => {
    await page.goto(url)
    for (const sel of ['link[rel="icon"][type="image/svg+xml"]', 'link[rel="icon"][sizes="32x32"]', 'link[rel="apple-touch-icon"]', 'link[rel="manifest"]']) {
      const href = await page.locator(sel).getAttribute('href')
      expect(href, `${name} ${sel}`).toBeTruthy()
      const res = await request.get(new URL(href, page.url()).toString())
      expect(res.status(), `${name} ${href}`).toBe(200)
    }
  })
}

test('manifest 의 아이콘 PNG 가 manifest 와 같은 폴더에서 200', async ({ page, request }) => {
  await page.goto(`${WIDGET_URL}/embed/landing.html`)
  const href = new URL(await page.locator('link[rel="manifest"]').getAttribute('href'), page.url()).toString()
  const manifest = await (await request.get(href)).json()
  expect(manifest.name).toBe('Sandcorp Flow')
  for (const icon of manifest.icons) {
    expect((await request.get(new URL(icon.src, href).toString())).status()).toBe(200)
  }
})

test('루트용 favicon.ico 파일이 위젯 public 에 있다(nginx /favicon.ico 의 원본)', async ({ request }) => {
  const res = await request.get(`${WIDGET_URL}/embed/favicon.ico`)
  expect(res.status()).toBe(200)
})

// 라이트·다크 × 데스크톱·390을 한 시험씩 나눈다 — 한 시험이 화면 12개를 열면 3초 한도를 넘는다(#257).
for (const scheme of ['light', 'dark']) {
  for (const [w, h] of [[1280, 800], [390, 800]]) {
    test(`랜딩·admin 로그인 화면에 Flow 로고가 실제로 그려진다 (${scheme}, ${w})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme })
      // admin 은 class 기반 테마(admin_theme 키) — 같은 키를 랜딩도 읽는다.
      await page.addInitScript((t) => { try { localStorage.setItem('admin_theme', t) } catch (e) {} }, scheme)
      await page.setViewportSize({ width: w, height: h })
      await page.goto(`${WIDGET_URL}/embed/landing.html`)
      const img = page.locator('a.brand img.flow-mark')
      await expect(img).toBeVisible()
      await expect.poll(() => img.evaluate((i) => i.complete && i.naturalWidth > 0)).toBe(true)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
      await shot(page, `landing-${scheme}-${w}`)

      await page.goto(`${ADMIN_URL}/admin-ui/`)
      const login = page.locator('img[src$="flow/icon.svg"]').first()
      await expect(login).toBeVisible()
      await expect.poll(() => login.evaluate((i) => i.complete && i.naturalWidth > 0)).toBe(true)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
      await shot(page, `admin-login-${scheme}-${w}`)

      // 로그인 없는 /try 머리줄(TryShell)의 로고
      await page.goto(`${ADMIN_URL}/admin-ui/try`)
      const tryMark = page.locator('header img[src$="flow/icon.svg"]')
      await expect(tryMark).toBeVisible()
      await expect.poll(() => tryMark.evaluate((i) => i.complete && i.naturalWidth > 0)).toBe(true)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
      await shot(page, `try-${scheme}-${w}`)
    })
  }
}
