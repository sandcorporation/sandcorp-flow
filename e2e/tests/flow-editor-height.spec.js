// 편집기 3단 높이(#103): 체험 배너가 있을 때도 캔버스 +/−/맞춤 컨트롤이 첫 화면(뷰포트) 안에 있어야 한다.
// 플랫폼 키 없는 dev 설정은 배너를 띄우지 않으므로 /api/tenant/trial/ 응답을 체험 중으로 바꿔 배너를 띄운다.
const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { settled } = require('./settle')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const TAG = process.env.SHOT_TAG || 'after'

for (const [w, h] of [[1600, 900], [1280, 720]]) {
  test(`배너 있는 ${w}x${h}: 캔버스 컨트롤이 뷰포트 안`, async ({ page }) => {
    test.setTimeout(90_000)
    const tenant = await provisionTenant(`Editor Height ${Date.now()}`)
    const graph = JSON.parse(fs.readFileSync(path.join(__dirname, 'flow-showcase.json'), 'utf8'))
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '높이', graph } })).json()
    await page.route('**/api/tenant/trial/', (r) => r.fulfill({ json: { state: 'trial', remaining: 17, limit: 20 } }))
    await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
    await page.setViewportSize({ width: w, height: h })
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(graph.nodes.length)
    await expect(page.getByTestId('trial-banner')).toBeVisible()
    await settled(page)
    const m = await page.evaluate(() => {
      const c = document.querySelector('.react-flow__controls').getBoundingClientRect()
      const canvas = document.querySelector('[data-testid=flow-canvas]').getBoundingClientRect()
      return { controlsBottom: Math.round(c.bottom), canvasBottom: Math.round(canvas.bottom), innerHeight, scrollY, docOverflow: document.documentElement.scrollHeight - innerHeight }
    })
    console.log('[metrics]', TAG, w, h, JSON.stringify(m))
    if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, `${TAG}-editor-banner-${w}x${h}.png`) }) }
    expect(m.controlsBottom).toBeLessThanOrEqual(m.innerHeight)
    expect(m.canvasBottom).toBeLessThanOrEqual(m.innerHeight)
  })
}
