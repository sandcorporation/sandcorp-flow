// /try 체험·공개 보기 콘솔 401 정리 + 폰 시트 자동 팬(#150) — 실제 스택.
// SHOTS_DIR 이 있으면 390폭 노드 탭 뒤 사진(라이트·다크)을 남긴다. SHOT_TAG=before 면 전 사진.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')
const { provisionTenant } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${process.env.SHOT_TAG || 'after'}-${name}.png`) })
}

/** 응답 상태가 4xx 인 것을 모은다(콘솔 401 오류의 근원). */
function watch4xx(page) {
  const bad = []
  page.on('response', (r) => { if (r.status() >= 400 && r.status() < 500) bad.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`) })
  return bad
}

test.describe('체험 모드 콘솔 정리·폰 시트 팬(#150)', () => {
  test('/try 진입 → 고쳐 보기 → HTTP 노드 시트까지 4xx 응답 0', async ({ page }) => {
    test.setTimeout(120_000)
    const bad = watch4xx(page)
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto(`${ADMIN_URL}/admin-ui/try`)
    await page.locator('[data-template="shop_support"]').getByRole('button', { name: '고쳐 보기' }).click()
    await expect(page).toHaveURL(/\/try\/flow\//)
    await page.locator('[data-node-id="order"]').click()
    await expect(page.getByText('가짜 API').first()).toBeVisible()
    await page.waitForTimeout(800)
    expect(bad).toEqual([])
  })

  test('공개 흐름 보기도 4xx 응답 0', async ({ page, request }) => {
    test.setTimeout(120_000)
    const { token } = await provisionTenant(`401정리 ${Date.now()}`, 15000, { defaultFlow: true })
    const auth = { Authorization: `Bearer ${token}` }
    const slug = `pub-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    expect((await request.patch(`${API_URL}/api/tenant/slug/`, { headers: auth, data: { slug } })).ok()).toBe(true)
    const flows = await (await request.get(`${API_URL}/api/tenant/flows/`, { headers: auth })).json()
    const flow = flows.find((f) => f.is_public)
    const graph = (await (await request.get(`${API_URL}/api/try/templates/shop_support/`)).json()).graph
    expect((await request.put(`${API_URL}/api/tenant/flows/${flow.id}/draft/`, { headers: auth, data: { graph } })).ok()).toBe(true)
    const saved = await (await request.post(`${API_URL}/api/tenant/flows/${flow.id}/versions/`, { headers: auth, data: { note: 'e2e' } })).json()
    expect((await request.post(`${API_URL}/api/tenant/flows/${flow.id}/deploy/`, { headers: auth, data: { version: saved.version.number } })).ok()).toBe(true)
    expect((await request.patch(`${API_URL}/api/tenant/config/`, { headers: auth, data: { public_flow_view: true } })).ok()).toBe(true)

    const bad = watch4xx(page)
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto(`${ADMIN_URL}/admin-ui/flows/${slug}`)
    await page.locator('[data-node-id="order"]').click()
    await page.waitForTimeout(800)
    expect(bad).toEqual([])
  })

  for (const scheme of ['light', 'dark']) {
    test(`390폭 ${scheme}: 노드를 누르면 그 노드가 시트 위 보이는 영역 안으로 온다`, async ({ page }) => {
      test.setTimeout(120_000)
      await page.emulateMedia({ colorScheme: scheme })
      await page.setViewportSize({ width: 390, height: 844 })
      await page.goto(`${ADMIN_URL}/admin-ui/try`)
      await page.locator('[data-template="shop_support"]').getByRole('button', { name: '고쳐 보기' }).click()
      await expect(page).toHaveURL(/\/try\/flow\//)
      await expect(page.locator('.react-flow__node').first()).toBeVisible()
      // 캔버스 아래쪽(시트에 가려질 자리)에 있는 노드를 고른다
      const ids = await page.locator('.react-flow__node').evaluateAll((els) => els.map((e) => ({ id: e.getAttribute('data-id'), y: e.getBoundingClientRect().bottom })))
      const target = ids.sort((a, b) => b.y - a.y)[0].id
      // 화면 밖이면 클릭이 안 되니, 보이는 범위까지 끌어 올려 둔다
      await page.locator(`[data-node-id="${target}"]`).dispatchEvent('click')
      const sheet = page.getByTestId('phone-sheet')
      await expect(sheet).toBeVisible()
      const sheetTop = (await sheet.boundingBox()).y
      const canvasTop = (await page.getByTestId('flow-canvas').boundingBox()).y
      await expect.poll(async () => {
        const b = await page.locator(`[data-node-id="${target}"]`).boundingBox()
        return b && b.y >= canvasTop - 1 && b.y + b.height <= sheetTop + 1
      }, { timeout: 5000 }).toBe(true)
      await shot(page, `phone-pan-${scheme}`)
    })
  }
})
