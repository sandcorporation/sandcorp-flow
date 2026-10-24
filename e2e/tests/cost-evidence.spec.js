// 증거 사진 전용(SHOTS_DIR 이 있을 때만, #85): custom LLM Provider 도움말과 새로고침 뒤 복원된 판단 기록을 라이트·다크로 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const TAG = process.env.SHOT_TAG || 'after'
const shot = async (page, name) => {
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${TAG}-${name}.png`) })
}

test.describe('비용 표시 증거 사진', () => {
  test.skip(!SHOTS, 'SHOTS_DIR 이 있을 때만')

  test('Provider 도움말·판단 기록 복원 — 라이트·다크', async ({ page }) => {
    test.setTimeout(120_000)
    const tenant = await provisionTenant(`Cost Shots ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const graph = { nodes: [
      { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
      { id: 'draft', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 40 }, config: { instructions: '짧게 답한다.' } },
    ], edges: [{ from: 'start', to: 'draft' }] }
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '비용 흐름', graph } })).json()
    await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
    await page.setViewportSize({ width: 1600, height: 900 })
    await page.reload()
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(aside.getByRole('region', { name: '시험 대화' }).locator('[data-reply]')).toBeVisible({ timeout: 60_000 })

    for (const theme of ['light', 'dark']) {
      await page.evaluate((t) => localStorage.setItem('admin_theme', t), theme)
      await page.reload() // 새로고침 — 판단 기록은 저장된 기록에서 되돌아온다
      await aside.getByRole('tab', { name: '판단 기록' }).click()
      await expect(aside.getByRole('region', { name: '판단 기록' }).getByText(/세션 합계/)).toBeVisible()
      await shot(page, `log-restored-${theme}`)
    }
    for (const theme of ['light', 'dark']) {
      await page.evaluate((t) => localStorage.setItem('admin_theme', t), theme)
      await page.goto(`${ADMIN_URL}/admin-ui/tenant/config`)
      await page.getByRole('tab', { name: 'AI 모델' }).click()
      await page.getByRole('button', { name: /고급 설정/ }).click()
      await page.getByLabel('LLM Provider 타입').selectOption('custom')
      await expect(page.getByText(/stream_options\(include_usage\)/)).toBeVisible()
      await shot(page, `provider-help-${theme}`)
    }
  })
})
