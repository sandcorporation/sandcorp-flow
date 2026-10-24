const { test, expect } = require('@playwright/test')
const { takeOnboardingTicket } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'

// 온보딩(#41): sandcorp-auth 콜백은 멤버십이 없는 사람을 ?onboarding=1로 보내며 member_ticket 쿠키(경로
// /api/auth/member)를 심는다. 실제 sandcorp-auth를 부를 수 없으므로 시드가 만든 새 사람의 표를 쿠키로 넣고 시작한다.
test.describe('온보딩 — 조직 만들기 → 조직 화면 (#41)', () => {
  test('멤버십 없는 사람이 조직을 만들면 관리자로 바로 들어가고, 새로고침해도 유지된다', async ({ page, context }) => {
    const ticket = await takeOnboardingTicket()
    await context.addCookies([
      { name: 'member_ticket', value: ticket, domain: new URL(ADMIN_URL).hostname, path: '/api/auth/member' },
    ])
    const suffix = Date.now().toString(36)
    const orgName = `E2E 새 조직 ${suffix}`

    await page.goto(`${ADMIN_URL}/admin-ui/tenant?onboarding=1`)
    await expect(page.getByText(/E2E 새 고객님, 아직 속한 조직이 없습니다/)).toBeVisible()
    await page.getByLabel('조직 이름').fill(orgName)
    await expect(page.getByLabel('공개 주소')).toHaveValue(`E2E-새-조직-${suffix}`)
    await page.getByLabel('공개 주소').fill(`e2e-org-${suffix}`)
    await page.getByRole('button', { name: '조직 만들기' }).click()

    // 조직 화면: 상단에 새 조직 이름과 내 이름, 관리자라 팀원 탭에서 나를 관리자로 본다.
    await expect(page).toHaveURL(/\/admin-ui\/tenant\/documents/)
    await expect(page.getByText(orgName).first()).toBeVisible()
    await expect(page.getByText('E2E 새 고객').first()).toBeVisible()

    await page.reload()
    await expect(page.getByText(orgName).first()).toBeVisible()
    await page.getByRole('link', { name: '팀원' }).click()
    const me = page.getByRole('row', { name: /E2E 새 고객/ })
    await expect(me).toBeVisible()
    await expect(me.getByText('관리자', { exact: true })).toBeVisible()
  })
})
