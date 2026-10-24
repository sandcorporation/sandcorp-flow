const { test, expect, request } = require('@playwright/test')
const { operatorToken } = require('./operatorToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'

/**
 * E2E 테스트용 테넌트와 에이전트를 API로 직접 생성한다.
 */
async function createTestTenantAndAgent() {
  const apiContext = await request.newContext({ baseURL: API_URL })

  // Operator 로그인
  const opToken = operatorToken()

  // 테넌트 생성 (에이전트 계정 포함 반환)
  const tenantRes = await apiContext.post('/api/operator/tenants/', {
    headers: { Authorization: `Bearer ${opToken}` },
    data: { name: `E2E Tenant ${Date.now()}` },
  })
  const tenant = await tenantRes.json()
  await apiContext.dispose()

  return {
    tenantName: tenant.name,
    agentUsername: tenant.agent_username,
    agentPassword: tenant.agent_temp_password,
    tenantKey: tenant.tenant_key,
  }
}

async function loginAsTenantAgent(page, tenantData) {
  await page.fill('input[placeholder="Tenant 이름"]', tenantData.tenantName)
  await page.fill('input[placeholder="사용자명"]', tenantData.agentUsername)
  await page.fill('input[placeholder="비밀번호"]', tenantData.agentPassword)
  await page.click('button[type="submit"]')
  await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 5000 })
}

test.describe('Tenant Agent 플로우', () => {
  let tenantData

  test.beforeAll(async () => {
    tenantData = await createTestTenantAndAgent()
  })

  test.beforeEach(async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/`)
    await page.evaluate(() => localStorage.clear())
  })

  test('Tenant Agent 로그인 성공', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/tenant`)
    await loginAsTenantAgent(page, tenantData)
  })

  test('설정 탭에서 System Prompt를 수정하고 저장할 수 있다', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/tenant`)
    await loginAsTenantAgent(page, tenantData)

    // 설정 탭 클릭
    await page.click('a:has-text("설정")')
    const promptInput = page.locator('[data-testid="system-prompt-input"]')
    await expect(promptInput).toBeVisible()

    // System Prompt 수정 ("Slug 저장" 버튼과 모호하지 않게 exact 셀렉터 사용)
    await promptInput.fill('E2E 테스트용 프롬프트입니다.')
    await page.getByRole('button', { name: '저장', exact: true }).click()

    await expect(page.locator('text=저장됨')).toBeVisible({ timeout: 3000 })
  })

  test('에이전트 탭에서 새 에이전트 계정을 추가할 수 있다', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/tenant`)
    await loginAsTenantAgent(page, tenantData)

    await page.click('a:has-text("팀원")')
    const newUsername = `agent_${Date.now()}`
    await page.fill('input[placeholder*="사용자명"]', newUsername)
    await page.click('button:has-text("추가")')

    await expect(page.locator(`text=${newUsername}`).first()).toBeVisible({ timeout: 5000 })
  })
})
