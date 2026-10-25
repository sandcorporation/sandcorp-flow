// #157: 캔버스 색 모드는 OS 가 아니라 앱 테마(admin_theme / html.dark)를 따른다. /try 는 로그인이 필요 없다.
// SHOTS_DIR 이 있으면 390폭 두 조합 증거 사진을 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const SHOTS = process.env.SHOTS_DIR

async function open(page, os, app) {
  await page.emulateMedia({ colorScheme: os })
  await page.addInitScript((v) => localStorage.setItem('admin_theme', v), app)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
  await page.locator('[data-template="sandcorp_home"]').getByRole('button', { name: '고쳐 보기' }).click()
  await expect(page).toHaveURL(/\/try\/flow\//)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
}
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`) })
}

test.describe('캔버스 테마 — 앱 테마를 따른다(#157)', () => {
  test('OS 다크 + 앱 라이트 → 캔버스도 라이트', async ({ page }) => {
    await open(page, 'dark', 'light')
    await expect(page.locator('html')).not.toHaveClass(/dark/)
    await expect(page.locator('.react-flow')).not.toHaveClass(/\bdark\b/)
    await expect(page.locator('.react-flow')).toHaveClass(/\blight\b/)
    await shot(page, 'os-dark-app-light')
  })
  test('OS 라이트 + 앱 다크 → 캔버스도 다크', async ({ page }) => {
    await open(page, 'light', 'dark')
    await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(page.locator('.react-flow')).toHaveClass(/\bdark\b/)
    await shot(page, 'os-light-app-dark')
  })
})
