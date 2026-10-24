const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'

async function setup() {
  const api = await request.newContext({ baseURL: API_URL })

  const tenant = await provisionTenant(`Visitors E2E ${Date.now()}`)

  const agentToken = tenant.token

  // 공개 슬러그 설정 — 방문자는 slug로 챗 스트림에 연결한다(ADR-0011, embed_token 폐지)
  const slug = `vis-e2e-${Date.now()}`
  await api.patch('/api/tenant/slug/', {
    headers: { Authorization: `Bearer ${agentToken}` },
    data: { slug },
  })

  await api.dispose()
  return { tenant, agentToken, slug }
}

async function loginAsTenantAgent(page, tenant) {
  await openMemberSession(page, ADMIN_URL, tenant.token)
  await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 10000 })
}

async function createVisitorSession(api, slug, visitorId, messages = []) {
  // 공개 slug + visitor_id로 챗 스트림 연결 → 세션 생성(ADR-0011). SSE는 무한 연결이므로
  // 응답 헤더에서 X-Session-Id를 읽은 뒤 즉시 abort한다.
  const controller = new AbortController()
  const res = await fetch(`${API_URL}/api/chat/stream?slug=${slug}&visitor_id=${visitorId}`, {
    signal: controller.signal,
  })
  const sessionId = res.headers.get('x-session-id')
  controller.abort()

  for (const msg of messages) {
    await api.post('/api/chat/message', {
      data: { session_id: sessionId, content: msg },
    })
  }

  return sessionId
}

test.describe('Visitors 탭', () => {
  let tenant, agentToken, slug

  test.beforeAll(async () => {
    const ctx = await setup()
    tenant = ctx.tenant
    agentToken = ctx.agentToken
    slug = ctx.slug
  })

  test.beforeEach(async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/`)
    await page.evaluate(() => localStorage.clear())
  })

  // ── Tracer Bullet ───────────────────────────────────────────────────────
  test('Visitors 탭이 탭 목록에 표시된다', async ({ page }) => {
    await loginAsTenantAgent(page, tenant)
    await expect(page.locator('a:has-text("Visitors")')).toBeVisible()
  })

  // ── 방문자 목록 ────────────────────────────────────────────────────────
  test('Visitors 탭 클릭 시 방문자 목록이 자동 로드된다', async ({ page }) => {
    const api = await request.newContext({ baseURL: API_URL })
    await createVisitorSession(api, slug, `v-e2e-list-${Date.now()}`)
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    await page.click('a:has-text("Visitors")')
    // visitor_id 검색 입력 placeholder가 표시되면 VisitorsTab이 렌더됨
    await expect(page.locator('input[placeholder*="visitor_id 검색"]')).toBeVisible({ timeout: 3000 })
  })

  test('방문자를 검색하면 해당 visitor_id만 표시된다', async ({ page }) => {
    const ts = Date.now()
    const api = await request.newContext({ baseURL: API_URL })
    await createVisitorSession(api, slug, `v-search-apple-${ts}`)
    await createVisitorSession(api, slug, `v-search-banana-${ts}`)
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    await page.click('a:has-text("Visitors")')
    // 초기 로드 완료 대기 (banana도 보여야 함)
    await expect(page.locator(`text=v-search-banana-${ts}`)).toBeVisible({ timeout: 5000 })
    await page.fill('input[placeholder*="visitor_id 검색"]', 'apple')
    await page.keyboard.press('Enter')

    await expect(page.locator(`text=v-search-apple-${ts}`)).toBeVisible({ timeout: 3000 })
    await expect(page.locator(`text=v-search-banana-${ts}`)).not.toBeVisible()
  })

  // ── 세션 목록 ──────────────────────────────────────────────────────────
  test('방문자 클릭 시 오른쪽 패널에 세션 목록이 표시된다', async ({ page }) => {
    const ts = Date.now()
    const visitorId = `v-session-list-${ts}`
    const api = await request.newContext({ baseURL: API_URL })
    await createVisitorSession(api, slug, visitorId)
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    await page.click('a:has-text("Visitors")')
    await page.locator(`text=${visitorId}`).first().click()

    await expect(page.locator('text=세션 목록')).toBeVisible({ timeout: 3000 })
  })

  // ── 세션 상세 — 대화 내역 ─────────────────────────────────────────────
  test('세션 클릭 시 대화 내역 탭에서 메시지를 볼 수 있다', async ({ page }) => {
    const ts = Date.now()
    const visitorId = `v-msg-detail-${ts}`
    const api = await request.newContext({ baseURL: API_URL })
    const sessionId = await createVisitorSession(api, slug, visitorId, [
      `E2E 테스트 메시지 ${ts}`,
    ])
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    await page.click('a:has-text("Visitors")')
    await page.locator(`text=${visitorId}`).first().click()
    // 세션 카드 클릭
    await page.locator(`text=${sessionId.slice(0, 8)}`).first().click()

    await expect(page.locator('button:has-text("대화 내역")')).toBeVisible({ timeout: 3000 })
    await expect(page.locator(`text=E2E 테스트 메시지 ${ts}`)).toBeVisible({ timeout: 3000 })
  })

  test('"← 뒤로" 버튼 클릭 시 세션 목록으로 돌아간다', async ({ page }) => {
    const ts = Date.now()
    const visitorId = `v-back-btn-${ts}`
    const api = await request.newContext({ baseURL: API_URL })
    const sessionId = await createVisitorSession(api, slug, visitorId)
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    await page.click('a:has-text("Visitors")')
    await page.locator(`text=${visitorId}`).first().click()
    await page.locator(`text=${sessionId.slice(0, 8)}`).first().click()
    await page.click('button:has-text("← 뒤로")')

    await expect(page.locator('text=세션 목록')).toBeVisible({ timeout: 3000 })
  })

  // ── Checkpoint ─────────────────────────────────────────────────────────
  test('Checkpoint 탭에서 AI 미호출 세션은 안내 문구가 표시된다', async ({ page }) => {
    const ts = Date.now()
    const visitorId = `v-no-checkpoint-${ts}`
    const api = await request.newContext({ baseURL: API_URL })
    const sessionId = await createVisitorSession(api, slug, visitorId)
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    await page.click('a:has-text("Visitors")')
    await page.locator(`text=${visitorId}`).first().click()
    await page.locator(`text=${sessionId.slice(0, 8)}`).first().click()
    await page.click('button:has-text("Checkpoint")')

    await expect(page.locator('text=AI 호출 내역이 없습니다')).toBeVisible({ timeout: 3000 })
  })
})
