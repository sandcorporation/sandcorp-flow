// 사용량 화면의 비용(#85): 실제 스택(Fake LLM, 단가표에 있는 모델명)에서 시험 한 턴을 돌린 뒤 테넌트·Operator 사용량 화면에
// 비용이 보이는지, 390폭에서 가로로 넘치지 않는지 잰다. SHOTS_DIR 이 있으면 라이트·다크 증거 사진도 남긴다
// (사진은 단가 모름·큰 금액을 보이도록 응답을 가짜로 바꾼 장면 포함).
const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { openOperatorSession } = require('./operatorToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const TAG = process.env.SHOT_TAG || 'after'
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${TAG}-${name}.png`), fullPage: true })
}

const overflow = (page) => page.evaluate(() => {
  const bad = []
  if (document.documentElement.scrollWidth > document.documentElement.clientWidth) bad.push('page')
  document.querySelectorAll('table, [class*="overflow"]').forEach((el) => {
    if (el.scrollWidth > el.clientWidth + 1) bad.push(`${el.tagName}.${String(el.className).slice(0, 30)} ${el.scrollWidth}>${el.clientWidth}`)
  })
  return bad
})

const GRAPH = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
    { id: 'draft', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 40 }, config: { instructions: '짧게 답한다.' } },
  ],
  edges: [{ from: 'start', to: 'draft' }],
}

const MOCK_TENANT = {
  total_tokens: 1234567, cost_usd: '12.345678', unpriced_models: ['my-custom-model-v2'], unpriced_requests: 3,
  by_call_type: [
    { call_type: 'chat', input_tokens: 900000, output_tokens: 300000, total_tokens: 1200000, request_count: 420, cost_usd: '12.300000', unpriced_models: [], unpriced_requests: 0 },
    { call_type: 'extraction', input_tokens: 30000, output_tokens: 4567, total_tokens: 34567, request_count: 12, cost_usd: '0.045678', unpriced_models: ['my-custom-model-v2'], unpriced_requests: 3 },
  ],
  daily: [
    { date: '2026-10-22', total_tokens: 400000, cost_usd: '4.100000', has_unpriced: false },
    { date: '2026-10-23', total_tokens: 434567, cost_usd: '4.145678', has_unpriced: true },
    { date: '2026-10-24', total_tokens: 400000, cost_usd: '4.100000', has_unpriced: false },
  ],
}
const MOCK_OPERATOR = {
  total_tokens: 5000000, cost_usd: '45.670000', unpriced_models: ['my-custom-model-v2'], unpriced_requests: 3,
  by_tenant: [
    { tenant_id: 't1', tenant_name: '아주 긴 이름을 가진 고객사 주식회사 샌드코프 플로우', total_tokens: 3000000, request_count: 900, cost_usd: '30.120000', unpriced_models: [], unpriced_requests: 0 },
    { tenant_id: 't2', tenant_name: '보통 고객사', total_tokens: 2000000, request_count: 400, cost_usd: '15.550000', unpriced_models: ['my-custom-model-v2'], unpriced_requests: 3 },
  ],
  daily: MOCK_TENANT.daily,
}

test.describe('사용량 화면 — 비용', () => {
  test('시험 한 턴 뒤 테넌트·Operator 사용량에 비용이 보이고(단가 모름 아님) 390폭에서 넘치지 않는다', async ({ page }) => {
    test.setTimeout(150_000)
    const tenant = await provisionTenant(`Usage Cost ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '비용 흐름', graph: GRAPH } })).json()
    const turn = await api.post(`/api/tenant/flows/${made.id}/test/`, { data: { message: '안녕하세요' } })
    expect(turn.ok()).toBeTruthy()
    expect(await turn.text()).toContain('event: done')

    await page.setViewportSize({ width: 1280, height: 900 })
    await openMemberSession(page, ADMIN_URL, tenant.token, 'tenant/usage')
    await expect(page.getByTestId('usage-total')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('usage-cost')).toHaveText(/^\$\d+\.\d{4}$/)
    await expect(page.getByTestId('usage-cost-unknown')).toHaveCount(0) // Fake LLM 모델명이 단가표에 있다
    await expect(page.getByTestId('daily-cost-row').first()).toContainText('$')
    await shot(page, 'usage-tenant-real-1280')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.reload()
    await expect(page.getByTestId('usage-cost')).toBeVisible()
    expect(await overflow(page)).toEqual([])

    await openOperatorSession(page, ADMIN_URL)
    await page.goto(`${ADMIN_URL}/admin-ui/operator/usage`)
    await expect(page.getByTestId('usage-cost')).toHaveText(/^\$\d/)
    await expect(page.getByTestId('daily-cost-row').first()).toContainText('$')
    expect(await overflow(page)).toEqual([])
  })

  test('단가 모름 모델이 있는 큰 금액도 390폭에서 넘치지 않는다(가짜 응답) + 증거 사진', async ({ page }) => {
    test.setTimeout(90_000)
    await page.route('**/api/tenant/usage/**', (r) => r.fulfill({ json: MOCK_TENANT }))
    await page.route('**/api/operator/usage/**', (r) => r.fulfill({ json: MOCK_OPERATOR }))
    const tenant = await provisionTenant(`Usage Cost Mock ${Date.now()}`)
    for (const theme of ['light', 'dark']) {
      await page.addInitScript((t) => localStorage.setItem('admin_theme', t), theme)
      for (const [w, h] of [[1280, 900], [390, 844]]) {
        await page.setViewportSize({ width: w, height: h })
        await openMemberSession(page, ADMIN_URL, tenant.token, 'tenant/usage')
        await page.evaluate((t) => localStorage.setItem('admin_theme', t), theme)
        await page.reload()
        await expect(page.getByTestId('usage-cost-unknown')).toContainText('my-custom-model-v2')
        await expect(page.getByTestId('daily-cost-row')).toHaveCount(3)
        expect(await overflow(page)).toEqual([])
        await shot(page, `usage-tenant-${theme}-${w}`)

        await openOperatorSession(page, ADMIN_URL)
        await page.goto(`${ADMIN_URL}/admin-ui/operator/usage`)
        await expect(page.getByTestId('tenant-row')).toHaveCount(2)
        expect(await overflow(page)).toEqual([])
        await shot(page, `usage-operator-${theme}-${w}`)
      }
    }
  })
})
