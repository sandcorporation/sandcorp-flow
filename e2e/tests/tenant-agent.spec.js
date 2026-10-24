const { test, expect } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'

// 고객 로그인은 sandcorp-auth 콜백뿐이다(#40) — 로그인 화면은 버튼·리다이렉트 시작까지만 보고,
// 들어간 뒤의 흐름은 시드한 멤버 세션(seed_e2e_member --serve-tenants-from)으로 연다.
// 팀원 탭(초대·내보내기)은 member-team.spec.js가 덮는다.
test.describe('고객 Admin 플로우', () => {
  let tenant

  test.beforeAll(async () => {
    tenant = await provisionTenant(`E2E Tenant ${Date.now()}`)
  })

  test.beforeEach(async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/`)
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  })

  test('로그인 화면은 비밀번호 대신 sandcorp 로그인 버튼을 보여 주고 누르면 인증 서버로 보낸다', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/tenant`)
    await expect(page.locator('input[type="password"]')).toHaveCount(0)
    const button = page.getByRole('link', { name: 'sandcorp 계정으로 로그인' })
    await expect(button).toBeVisible()

    // 리다이렉트 시작까지만 — 실제 sandcorp-auth는 부르지 않는다(요청만 보고 막는다).
    let started = ''
    await page.route('**/api/auth/authorize/member**', (route) => {
      started = route.request().url()
      return route.abort()
    })
    await button.click()
    await expect.poll(() => started).toContain('/api/auth/authorize/member')
  })

  test('시드한 멤버 세션으로 들어가 조직 화면이 열린다', async ({ page }) => {
    await openMemberSession(page, ADMIN_URL, tenant.token)
    await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 10000 })
    await expect(page.getByText(tenant.name).first()).toBeVisible()
  })

  test('설정 탭에서 System Prompt를 수정하고 저장할 수 있다', async ({ page }) => {
    await openMemberSession(page, ADMIN_URL, tenant.token)
    await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 10000 })

    await page.click('a:has-text("설정")')
    const promptInput = page.locator('[data-testid="system-prompt-input"]')
    await expect(promptInput).toBeVisible()

    // "Slug 저장" 버튼과 모호하지 않게 exact 셀렉터 사용
    await promptInput.fill('E2E 테스트용 프롬프트입니다.')
    await page.getByRole('button', { name: '저장', exact: true }).click()

    await expect(page.locator('text=저장됨')).toBeVisible({ timeout: 3000 })
  })
})
