// 증거 사진 전용(좁은 쪽은 768폭 — 390폭은 폰 모드(#38)라 편집기 모양이 다르다, 그건 try-phone.spec.js)(SHOTS_DIR 이 있을 때만): 예제 흐름(노드 11개)으로 맞춤 보기·높이와 종류별 설정 폼을 라이트·다크·390폭으로 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { settled, trackRequests } = require('./settle')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const TAG = process.env.SHOT_TAG || 'after'

const shot = async (target, name) => {
  fs.mkdirSync(SHOTS, { recursive: true })
  await target.screenshot({ path: path.join(SHOTS, `${TAG}-${name}.png`) })
}

test.describe('흐름 편집기 증거 사진', () => {
  test.skip(!SHOTS, 'SHOTS_DIR 이 있을 때만')

  test('맞춤 보기·높이, 종류별 폼', async ({ page }) => {
    const net = trackRequests(page)
    const tenant = await provisionTenant(`Flow Shots ${Date.now()}`)
    const graph = JSON.parse(fs.readFileSync(path.join(__dirname, 'flow-showcase.json'), 'utf8'))
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '홈페이지 상담', graph } })).json()
    await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
    await page.setViewportSize({ width: 1600, height: 900 })
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(graph.nodes.length)
    await settled(page)

    const metrics = await page.evaluate(() => {
      const box = document.querySelector('[data-testid=flow-canvas]').getBoundingClientRect()
      const vp = new DOMMatrix(getComputedStyle(document.querySelector('.react-flow__viewport')).transform)
      const title = document.querySelector('.react-flow__node [data-node-title], .react-flow__node')
      const fs = parseFloat(getComputedStyle(title).fontSize)
      return { canvasTop: Math.round(box.top), canvasBottom: Math.round(box.bottom), innerHeight, zoom: +vp.a.toFixed(2), nodeFontPx: fs, renderedFontPx: +(fs * vp.a).toFixed(1) }
    })
    console.log('[metrics]', TAG, JSON.stringify(metrics))
    await page.locator('[data-node-id="guard"]').click()
    await settled(page)
    await shot(page, 'fit-1600x900')

    const aside = page.locator('aside[aria-label="노드 설정"]')
    const kinds = [['start', 'start'], ['classify', 'ai_classify'], ['route', 'condition'], ['ask', 'ai_answer'], ['cases', 'knowledge_search'], ['handoff', 'handoff'], ['guard', 'guardrail'], ['submit', 'http_tool']]
    for (const theme of ['light', 'dark']) {
      await page.evaluate((t) => localStorage.setItem('admin_theme', t), theme)
      await page.reload()
      await expect(page.locator('.react-flow__node')).toHaveCount(graph.nodes.length)
      for (const [id, kind] of kinds) {
        await page.locator(`[data-node-id="${id}"]`).click()
        await expect(aside.getByRole('tab', { name: '폼' })).toBeVisible()
        await settled(page)
        await shot(aside, `form-${kind}-${theme}`)
      }
      // 틀린 http 주소 → 필드 아래 오류(라이트·다크 한 장씩)
      await page.locator('[data-node-id="submit"]').click()
      await aside.getByLabel('주소(https)').fill('http://insecure.example.com/x')
      await aside.getByLabel('주소(https)').blur()
      await expect(aside.getByText(/https:\/\/ 로 시작해야/)).toBeVisible({ timeout: 10000 })
      await shot(page, `form-error-${theme}`)
      await aside.getByLabel('주소(https)').fill('https://api.example.com/inquiries')
      await aside.getByLabel('주소(https)').blur()
      await net.idle()
    }
    await page.evaluate(() => localStorage.setItem('admin_theme', 'light'))
    await page.setViewportSize({ width: 768, height: 844 })
    await page.reload()
    await expect(page.locator('.react-flow__node').first()).toBeVisible()
    await page.locator('[data-node-id="guard"]').click()
    await settled(page)
    await aside.scrollIntoViewIfNeeded()
    await shot(page, 'form-768')
    await api.dispose()
  })
})
