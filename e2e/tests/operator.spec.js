const { test, expect } = require('@playwright/test')
const { openOperatorSession } = require('./operatorToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'

// Operator 로그인은 sandcorp-auth 콜백뿐이다(#21). 실제 sandcorp-auth를 부를 수 없으므로
// 로그인 화면은 버튼·거절 화면까지만 보고, 들어간 뒤의 흐름은 시드한 세션(seed_e2e_operator)으로 연다.
test.describe('Operator 플로우', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/`)
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  })

  test('로그인 화면은 비밀번호 대신 sandcorp 로그인 버튼을 보여 준다', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/operator`)

    await expect(page.getByRole('button', { name: 'sandcorp 계정으로 로그인' })).toBeVisible()
    await expect(page.locator('input[type="password"]')).toHaveCount(0)
  })

  test('콜백이 거절하면 거절 화면을 보여 준다', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/operator?login_error=member`)

    await expect(page.locator('text=로그인할 수 없습니다')).toBeVisible()
    await expect(page.getByRole('button', { name: '다시 로그인' })).toBeVisible()
  })

  test('Operator가 새 테넌트를 생성할 수 있다', async ({ page }) => {
    await openOperatorSession(page, ADMIN_URL)
    await expect(page.locator('text=Tenant 목록')).toBeVisible()

    const tenantName = `E2E 테스트 테넌트 ${Date.now()}`
    await page.fill('input[placeholder="고객사 이름"]', tenantName)
    await page.click('button:has-text("추가")')

    await expect(page.locator(`text=${tenantName}`).first()).toBeVisible({ timeout: 5000 })
  })
})
