const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'

const HITL_SYSTEM_PROMPT =
  "You are a helpful customer service AI assistant. " +
  "When a user requests a human agent or uses the Korean word '상담원', " +
  "you MUST set needs_hitl=true. For all other requests respond helpfully with needs_hitl=false."

async function setupHitlTest() {
  const api = await request.newContext({ baseURL: API_URL })

  const tenant = await provisionTenant(`HITL E2E ${Date.now()}`)

  // 에이전트 로그인 후 HITL 유도 시스템 프롬프트 설정
  const agentToken = tenant.token

  await api.patch('/api/tenant/config/', {
    headers: { Authorization: `Bearer ${agentToken}` },
    data: {
      model_id: 'qwen2.5:3b',
      system_prompt: HITL_SYSTEM_PROMPT,
    },
  })

  // 공개 슬러그 설정 — 방문자는 /chatbot/{slug}/로 위젯에 연결한다(ADR-0011, embed_token 폐지)
  const slug = `hitl-e2e-${Date.now()}`
  await api.patch('/api/tenant/slug/', {
    headers: { Authorization: `Bearer ${agentToken}` },
    data: { slug },
  })

  await api.dispose()

  return {
    slug,
    tenant,
  }
}

// 위젯으로 HITL을 트리거하고 시스템 메시지가 뜰 때까지 기다린다.
// E2E 스택의 LLM은 결정적 Fake이므로 '상담원' 메시지는 항상 escalation된다(재시도 불필요).
// 방문자별 visitor_id로 세션을 격리한다. 위젯 base가 /embed/라 경로에 /chatbot/{slug}/를 포함시킨다.
async function escalateViaWidget(page, slug) {
  const visitorId = `hitl-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  await page.goto(`${WIDGET_URL}/embed/chatbot/${slug}/?visitor_id=${visitorId}`)
  const textarea = page.locator('textarea[placeholder="메시지를 입력하세요..."]')
  await expect(textarea).toBeEnabled({ timeout: 10000 })

  await textarea.fill('상담원 연결해 주세요')
  await page.click('button:has-text("전송")')
  await expect(page.locator('[data-role="system"]')).toBeVisible({ timeout: 20000 })
}

test.describe('HITL 전체 플로우', () => {

  let ctx

  test.beforeAll(async () => {
    ctx = await setupHitlTest()
  })

  test('방문자가 상담원 요청 시 HITL 시작 메시지가 위젯에 표시된다', async ({ page }) => {
    // escalateViaWidget이 [data-role="system"] 가시성을 보장하므로 별도 단언 불필요
    await escalateViaWidget(page, ctx.slug)
    await expect(page.locator('[data-role="system"]')).toBeVisible()
  })

  test('어드민 에이전트가 에스컬레이션을 확인하고 클레임할 수 있다', async ({ page }) => {
    // 이 테스트는 자체 escalation을 만들어 다른 테스트에 의존하지 않는다 (독립성)
    await escalateViaWidget(page, ctx.slug)

    await openMemberSession(page, ADMIN_URL, ctx.tenant.token)
    await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 10000 })

    await page.click('a:has-text("HITL")')

    // 여러 escalation이 있을 수 있으므로 first()로 하나를 claim한다
    const claimBtn = page.locator('button:has-text("수락하기")').first()
    await expect(claimBtn).toBeVisible({ timeout: 10000 })
    await claimBtn.click()

    await expect(page.locator('button:has-text("AI에게 넘기기")').first()).toBeVisible({ timeout: 5000 })
  })

  test('상담원이 escalation 없는 임의 세션을 직접 takeover할 수 있다', async ({ page, context }) => {
    // 방문자가 일반 메시지로 세션을 연다(escalation 아님) — '상담원' 단어를 쓰지 않는다.
    const visitorId = `take-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    const visitor = await context.newPage()
    await visitor.goto(`${WIDGET_URL}/embed/chatbot/${ctx.slug}/?visitor_id=${visitorId}`)
    const ta = visitor.locator('textarea[placeholder="메시지를 입력하세요..."]')
    await expect(ta).toBeEnabled({ timeout: 10000 })  // SSE 연결 → 세션 생성
    await ta.fill('안녕하세요, 영업시간이 궁금해요')
    await visitor.click('button:has-text("전송")')

    // 어드민 에이전트 로그인 → 상담 콘솔
    await openMemberSession(page, ADMIN_URL, ctx.tenant.token)
    await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 10000 })
    await page.click('a:has-text("HITL")')

    // "다른 세션" 목록에서 해당 방문자 행의 "상담 시작"으로 takeover
    const row = page.locator('li', { hasText: visitorId })
    await expect(row).toBeVisible({ timeout: 15000 })
    await row.locator('button:has-text("상담 시작")').click()

    // takeover → escalation 카드로 승격 → AI에게 넘기기(resolve) 노출
    await expect(page.locator('button:has-text("AI에게 넘기기")').first()).toBeVisible({ timeout: 10000 })
    await visitor.close()
  })
})
