const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR // 있으면 증거 스크린샷을 남긴다
const TAG = process.env.SHOT_TAG || 'after'

async function shot(page, name) {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${TAG}-${name}.png`) })
}

test.describe('흐름 보관 — 실제 스택 (#126)', () => {
  test('목록에서 보관(확인 한 번) → 새로고침해도 없음 · 위젯 흐름엔 버튼이 없음 · 390폭 넘침 없음', async ({ page }) => {
    test.setTimeout(120_000)
    const tenant = await provisionTenant(`Archive ${Date.now()}`, 15000, { defaultFlow: true })
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const name = `보관할 흐름 ${Date.now()}`
    const made = await api.post('/api/tenant/flows/', { data: { name } })
    expect(made.status()).toBe(201)
    const flowId = (await made.json()).id
    await openMemberSession(page, ADMIN_URL, tenant.token, 'tenant/flows')

    await expect(page.getByRole('link', { name })).toBeVisible()
    // 위젯이 쓰는 기본(공개) 흐름에는 보관 버튼이 없다.
    const buttons = page.getByRole('button', { name: /보관$/ })
    await expect(buttons).toHaveCount(1)

    await page.getByRole('button', { name: `${name} 보관` }).click()
    await expect(page.getByText(/보관하면 목록에서 사라집니다/)).toBeVisible()
    await shot(page, 'archive-confirm')

    await page.getByRole('button', { name: '보관 확인' }).click()
    await expect(page.getByRole('link', { name })).toHaveCount(0)
    await page.reload()
    await expect(page.getByRole('heading', { name: '흐름', exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name })).toHaveCount(0)

    // 서버 쪽도 같다: 조회 404, 목록에 없음, 같은 이름으로 다시 만들 수 있다.
    expect((await api.get(`/api/tenant/flows/${flowId}/`)).status()).toBe(404)
    const again = await api.post('/api/tenant/flows/', { data: { name } })
    expect(again.status()).toBe(201)

    await page.setViewportSize({ width: 390, height: 800 })
    await page.reload()
    await expect(page.getByRole('link', { name })).toBeVisible()
    await page.getByRole('button', { name: `${name} 보관` }).click()
    await shot(page, 'archive-confirm-390')
    const over = await page.evaluate(() => {
      const bad = []
      const root = document.documentElement
      if (root.scrollWidth > root.clientWidth) bad.push(`page ${root.scrollWidth}>${root.clientWidth}`)
      for (const el of document.querySelectorAll('table, [class*="overflow"]')) {
        if (el.scrollWidth > el.clientWidth + 1) bad.push(`${el.tagName}.${String(el.className).slice(0, 40)} ${el.scrollWidth}>${el.clientWidth}`)
      }
      return bad
    })
    expect(over).toEqual([])
    await api.dispose()
  })
})
