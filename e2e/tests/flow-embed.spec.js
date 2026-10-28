const { test, expect, request } = require('@playwright/test')
const { provisionTenant } = require('./memberToken')

// 흐름별 임베드(#246, ADR-0034): /chatbot/<slug>/<embed_key>/ 는 그 흐름이 답하고, 닫힌 키는 「열려 있지 않음」이다.
// 흐름을 켜는 API는 E2 몫이라 시드 대역(embed-flow)이 임베드를 켠 배포 흐름을 만들어 둔다.
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'

test.describe('흐름 키 주소', () => {
  let slug
  let embedKey

  test.beforeAll(async () => {
    const tenant = await provisionTenant(`Flow Embed E2E ${Date.now()}`, 15000, { embedFlow: true })
    embedKey = tenant.embedKey
    slug = `flow-embed-${Date.now()}`
    const api = await request.newContext({ baseURL: API_URL })
    await api.patch('/api/tenant/slug/', { headers: { Authorization: `Bearer ${tenant.token}` }, data: { slug } })
    await api.dispose()
  })

  test('키 주소로 그 흐름과 대화 한 턴을 한다', async ({ page }) => {
    const api = await request.newContext({ baseURL: API_URL })
    const resolved = await api.get(`/api/chat/resolve?slug=${slug}&flow=${embedKey}`)
    expect(resolved.status()).toBe(200)
    expect((await resolved.json()).flow_name).toBe('임베드 흐름')
    await api.dispose()

    await page.goto(`${WIDGET_URL}/embed/chatbot/${slug}/${embedKey}/?visitor_id=e2e-flow-embed`)
    const textarea = page.locator('textarea[placeholder="메시지를 입력하세요..."]')
    await expect(textarea).toBeEnabled()
    await textarea.fill('안녕하세요')
    await page.click('button:has-text("전송")')
    await expect(page.locator('[data-role="user"]')).toBeVisible()
    await expect(page.locator('[data-role="assistant"]')).toBeVisible()
  })

  test('닫힌 키 주소는 「열려 있지 않음」을 보이고 입력이 없다', async ({ page }) => {
    await page.goto(`${WIDGET_URL}/embed/chatbot/${slug}/zzzzzzzz/?visitor_id=e2e-flow-closed`)
    await expect(page.getByText('지금 열려 있지 않은 상담입니다.')).toBeVisible()
    await expect(page.locator('textarea')).toHaveCount(0)
  })
})
