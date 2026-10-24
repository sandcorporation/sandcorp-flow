const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'

// 테넌트 생성 + 모델/슬러그 설정 후 공개 슬러그를 반환한다(ADR-0011, embed_token 폐지).
async function setupWidgetTenant() {
  const apiContext = await request.newContext({ baseURL: API_URL })

  const tenant = await provisionTenant(`Widget E2E Tenant ${Date.now()}`)

  const agentToken = tenant.token

  await apiContext.patch('/api/tenant/config/', {
    headers: { Authorization: `Bearer ${agentToken}` },
    data: { model_id: 'qwen2.5:3b' },
  })

  const slug = `widget-e2e-${Date.now()}`
  await apiContext.patch('/api/tenant/slug/', {
    headers: { Authorization: `Bearer ${agentToken}` },
    data: { slug },
  })

  await apiContext.dispose()
  return slug
}

// 위젯 base가 /embed/라 경로에 /chatbot/{slug}/를 포함시켜 slug·assets를 둘 다 만족시킨다.
function widgetUrl(slug, visitorId) {
  return `${WIDGET_URL}/embed/chatbot/${slug}/?visitor_id=${visitorId}`
}

test.describe('Widget 채팅 플로우', () => {
  let slug

  test.beforeAll(async () => {
    slug = await setupWidgetTenant()
  })

  test('위젯이 로드되고 연결 대기 상태로 시작한다', async ({ page }) => {
    await page.goto(widgetUrl(slug, 'e2e-widget-load'))
    const textarea = page.locator('textarea[placeholder="메시지를 입력하세요..."]')
    await expect(textarea).toBeVisible({ timeout: 10000 })
  })

  test('SSE 연결 후 메시지 입력이 활성화된다', async ({ page }) => {
    await page.goto(widgetUrl(slug, 'e2e-widget-sse'))
    const textarea = page.locator('textarea[placeholder="메시지를 입력하세요..."]')
    await expect(textarea).toBeEnabled({ timeout: 10000 })
  })

  test('메시지 전송 후 AI 응답이 표시된다', async ({ page }) => {
    await page.goto(widgetUrl(slug, 'e2e-widget-msg'))
    const textarea = page.locator('textarea[placeholder="메시지를 입력하세요..."]')
    await expect(textarea).toBeEnabled({ timeout: 10000 })

    await textarea.fill('안녕하세요')
    await page.click('button:has-text("전송")')

    await expect(page.locator('[data-role="user"]')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('[data-role="assistant"]')).toBeVisible({ timeout: 30000 })
  })
})
