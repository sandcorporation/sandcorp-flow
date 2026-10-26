// 「이 흐름으로 가입」(#42): /try 고쳐 보기 → 가입 → (로그인 대역) → /try/claim → 옮긴 흐름이 테넌트 편집기에 초안으로.
// sandcorp-auth 왕복(authorize → 동의 → callback)은 외부 경계라 대역이다: 콜백이 하는 일(member_ticket 쿠키 + /try/claim 착지)을
// 시드 표(takeOnboardingTicket)와 authorize 주소 가로채기로 대신한다. 콜백 자체(then=try_claim → 표·착지)는 백엔드 시험 몫.
// 옮기기(/api/try/claim/)·온보딩·편집기는 실제 스택이다. SHOTS_DIR 이 있으면 증거 사진을 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')
const { takeOnboardingTicket, provisionTenant } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const SHOTS = process.env.SHOTS_DIR
const shot = async (page, name, clip) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), ...(clip ? { clip } : {}) })
}
const noOverflow = async (page) => {
  const wide = await page.evaluate(() => {
    const doc = document.documentElement
    const out = doc.scrollWidth > doc.clientWidth ? [`document ${doc.scrollWidth}>${doc.clientWidth}`] : []
    for (const el of document.querySelectorAll('body *')) {
      const cs = getComputedStyle(el)
      if (/(auto|scroll)/.test(cs.overflowX) && el.scrollWidth > el.clientWidth + 1) out.push(`${el.tagName}.${el.className} ${el.scrollWidth}>${el.clientWidth}`)
    }
    return out
  })
  expect(wide).toEqual([])
}

/** 「이 흐름으로 가입」: 세션이 있으면 링크, 아직 없으면(#195) 세션을 먼저 만드는 버튼 — #198 에서 다시 본다. */
const signupControl = (page) => page.getByRole('link', { name: '이 흐름으로 가입' }).or(page.getByRole('button', { name: '이 흐름으로 가입' }))

/** 「이 흐름으로 가입」 → (sandcorp-auth·콜백 대역) → /try/claim. */
async function signUpVia(page, onLoginRoute) {
  await page.route(/\/api\/auth\/authorize\/member\?then=try_claim$/, (route) =>
    route.fulfill({ status: 302, headers: { location: `${ADMIN_URL}/admin-ui/try/claim` } }))
  if (onLoginRoute) await onLoginRoute()
  await signupControl(page).click()
  await expect(page).toHaveURL(/\/admin-ui\/try\/claim$/)
}

async function startTry(page, width) {
  await page.setViewportSize({ width, height: width < 600 ? 844 : 900 })
  await page.goto(`${ADMIN_URL}/admin-ui/try?template=shop_support`) // 방문 즉시 편집기(#195) — 아직 세션 없음
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
}

for (const [label, width, scheme] of [['1600-light', 1600, 'light'], ['390-dark', 390, 'dark']]) {
  test(`새 가입자: 고쳐 보기 → 가입 → 새 테넌트 → 옮긴 흐름이 초안으로(${label})`, async ({ page, context }) => {
    test.setTimeout(120_000)
    await page.addInitScript((th) => localStorage.setItem('admin_theme', th), scheme)
    await startTry(page, width)
    const link = signupControl(page)
    await expect(link).toBeVisible()
    if (width < 600) await noOverflow(page)
    await shot(page, `signup-head-${label}`, { x: 0, y: 0, width, height: 120 })

    const ticket = await takeOnboardingTicket()
    await signUpVia(page, () => context.addCookies([
      { name: 'member_ticket', value: ticket, domain: new URL(ADMIN_URL).hostname, path: '/api/auth/member' },
    ]))
    const orgName = `E2E 옮기기 ${Date.now().toString(36)}`
    await page.getByLabel('조직 이름').fill(orgName)
    await expect(page.getByRole('radio')).toHaveCount(0) // Admin 테넌트가 없는 새 가입자는 고르지 않는다
    if (width < 600) await noOverflow(page)
    await shot(page, `claim-new-${label}`)
    await page.getByRole('button', { name: '만들고 흐름 옮기기' }).click()

    await expect(page).toHaveURL(/\/admin-ui\/tenant\/flows\/[0-9a-f-]+\?claimed=1$/)
    await expect(page.getByRole('heading', { name: '쇼핑몰 배송·반품 상담' })).toBeVisible()
    const note = page.getByTestId('claimed-notice')
    await expect(note).toContainText('체험에서 옮긴 흐름이에요')
    await expect(note).toContainText('지식 문서를 올리세요')
    await expect(note).toContainText('가짜 API')
    await expect(page.getByText(/v\d+ 배포 중/)).toHaveCount(0) // 초안만 — 배포하지 않았다
    if (width < 600) await noOverflow(page)
    await shot(page, `claimed-editor-${label}`)

    // 테넌트 흐름 목록: 기본 흐름(위젯) 옆에 옮긴 흐름
    await page.goto(`${ADMIN_URL}/admin-ui/tenant/flows`)
    await expect(page.getByText('쇼핑몰 배송·반품 상담')).toBeVisible()
    // 체험 세션은 지워졌다
    const gone = await page.evaluate(async () => (await fetch('/api/try/session/', { credentials: 'include' })).status)
    expect(gone).toBe(200)
    expect(await page.evaluate(async () => (await (await fetch('/api/try/session/', { credentials: 'include' })).json()))).toBeNull()
  })
}

test('기존 Admin 테넌트에 추가: 「기존 테넌트에 추가」를 골라 그 테넌트 편집기로', async ({ page }) => {
  test.setTimeout(120_000)
  const { name, token } = await provisionTenant(`E2E 기존 ${Date.now().toString(36)}`)
  await startTry(page, 1600)
  // 콜백 표의 테넌트 목록·선택은 대역(이 사용자는 시드 조직의 Admin) — 선택이 그 조직의 실제 세션 토큰을 준다.
  await signUpVia(page, async () => {
    await page.route('**/api/auth/member/tenants', (route) => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ name: 'E2E 고객', email: null, tenants: [{ tenant_id: 't-admin', tenant_name: name, role: 'admin' }, { tenant_id: 't-member', tenant_name: '남의 가게', role: 'member' }] }),
    }))
    await page.route('**/api/auth/member/select', (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ access_token: token }),
    }))
  })
  await page.getByRole('radio', { name: '기존 테넌트에 추가' }).check()
  await expect(page.getByRole('radio', { name: '남의 가게' })).toHaveCount(0) // Member 테넌트는 고를 수 없다
  await shot(page, 'claim-existing-1600-light')
  await page.getByRole('button', { name: '이 테넌트로 옮기기' }).click()

  await expect(page).toHaveURL(/\/admin-ui\/tenant\/flows\/[0-9a-f-]+\?claimed=1$/)
  await expect(page.getByRole('heading', { name: '쇼핑몰 배송·반품 상담' })).toBeVisible()
  await expect(page.getByTestId('claimed-notice')).toBeVisible()
})
