const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const ROUTE = 'tenant/config?section=secrets'

// 스펙마다 자기 조직(provisionTenant)을 써서 고정 계정 상태를 공유하지 않는다(#113).
test.describe('설정 > 비밀·허용 도메인 (#113)', () => {
  test('Admin: 도메인 추가·IP 거부·삭제, 비밀 추가 → 새로고침 → 이름만 보이고 값은 페이지 어디에도 없다', async ({ page }) => {
    const tenant = await provisionTenant(`Secrets ${Date.now()}`)
    const value = `sv-${Date.now()}-ZXCVBNM-98765`
    await openMemberSession(page, ADMIN_URL, tenant.token, ROUTE)

    // 허용 도메인: 와일드카드 설명 → 추가 → IP 는 서버 422 문구 → 삭제
    await expect(page.getByText(/example\.com.*하위 도메인/)).toBeVisible()
    await page.getByLabel('허용 도메인 패턴').fill('*.E2E-Example.com')
    await page.getByRole('button', { name: '도메인 추가' }).click()
    await expect(page.getByText('*.e2e-example.com', { exact: true })).toBeVisible()  // 서버가 정규화한 값
    await page.getByLabel('허용 도메인 패턴').fill('10.0.0.1')
    await page.getByRole('button', { name: '도메인 추가' }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await page.getByLabel('허용 도메인 패턴').fill('*.e2e-example.com')
    await page.getByRole('button', { name: '도메인 추가' }).click()
    await expect(page.getByRole('alert')).toBeVisible()  // 중복 409
    await page.getByRole('button', { name: '*.e2e-example.com 삭제' }).click()
    await expect(page.getByText('*.e2e-example.com', { exact: true })).toHaveCount(0)
    await page.reload()
    await expect(page.getByText('등록된 도메인이 없습니다.')).toBeVisible()

    // 비밀: 값 입력란은 password 형, 저장 후 비워지고, 새로고침해도 이름만 있다
    const valueInput = page.getByLabel('비밀 값')
    await expect(valueInput).toHaveAttribute('type', 'password')
    await page.getByLabel('비밀 이름').fill('E2E_TOKEN')
    await valueInput.fill(value)
    await page.getByRole('button', { name: '비밀 저장' }).click()
    await expect(page.getByText('E2E_TOKEN', { exact: true })).toBeVisible()
    await expect(valueInput).toHaveValue('')
    await page.reload()
    await expect(page.getByText('E2E_TOKEN', { exact: true })).toBeVisible()
    expect(await page.content()).not.toContain(value)
    expect(await page.evaluate(() => JSON.stringify([sessionStorage, localStorage]))).not.toContain(value)

    // 값 바꾸기 → 삭제(확인 한 번)
    await page.getByRole('button', { name: 'E2E_TOKEN 값 바꾸기' }).click()
    await page.getByLabel('E2E_TOKEN 새 값').fill(`${value}-new`)
    await page.getByRole('button', { name: '교체' }).click()
    await expect(page.getByLabel('E2E_TOKEN 새 값')).toHaveCount(0)
    expect(await page.content()).not.toContain(value)
    await page.getByRole('button', { name: 'E2E_TOKEN 삭제' }).click()
    await page.getByRole('button', { name: '삭제 확인' }).click()
    await expect(page.getByText('E2E_TOKEN', { exact: true })).toHaveCount(0)
  })

  test('Member 로 강등된 계정은 (토큰은 옛 Admin 이어도) 목록만 보이고 쓰기 컨트롤이 없다', async ({ page }) => {
    const tenant = await provisionTenant(`Secrets Member ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const members = await (await api.get('/api/tenant/members/')).json()
    const second = members.find((m) => m.name.includes('둘째'))
    expect((await api.patch(`/api/tenant/members/${second.id}/role`, { data: { role: 'member' } })).ok()).toBeTruthy()
    // 읽기는 된다 — 관리자가 만든 도메인이 Member 에게도 보인다
    expect((await api.post('/api/tenant/allowed-domains/', { data: { pattern: 'seen.example.com' } })).status()).toBe(201)

    await openMemberSession(page, ADMIN_URL, tenant.secondToken, ROUTE)
    await expect(page.getByText('seen.example.com', { exact: true })).toBeVisible()
    // 토큰은 로그인 때의 admin 이지만 화면은 서버 역할(members/me)을 따른다 — 쓰기 컨트롤이 없다(#120).
    await expect(page.getByText(/관리자만/).first()).toBeVisible()
    await expect(page.getByLabel('허용 도메인 패턴')).toHaveCount(0)
    await expect(page.getByLabel('비밀 값')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /삭제|값 바꾸기|도메인 추가|비밀 저장/ })).toHaveCount(0)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/member-secrets-after-demotion.png`, fullPage: true })
  })

  test('강등된 계정은 팀원·흐름 화면에서도 Admin 전용 컨트롤이 없다(#120)', async ({ page }) => {
    const tenant = await provisionTenant(`Role Server ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const members = await (await api.get('/api/tenant/members/')).json()
    const second = members.find((m) => m.name.includes('둘째'))
    expect((await api.patch(`/api/tenant/members/${second.id}/role`, { data: { role: 'member' } })).ok()).toBeTruthy()

    await openMemberSession(page, ADMIN_URL, tenant.secondToken, 'tenant/agents')
    await expect(page.getByText(/팀원 관리는 관리자만/)).toBeVisible()
    await expect(page.getByRole('button', { name: /초대/ })).toHaveCount(0)
    await page.goto(`${ADMIN_URL}/admin-ui/tenant/flows`)
    await expect(page.getByRole('heading', { name: '흐름', exact: true })).toBeVisible()
    await expect(page.getByText('새 흐름')).toHaveCount(0)
  })

  test('390폭: 긴 이름·긴 패턴이 있어도 어떤 요소도 가로로 넘치지 않는다', async ({ page }) => {
    const tenant = await provisionTenant(`Secrets Narrow ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const longName = 'A_VERY_LONG_SECRET_NAME_' + 'X'.repeat(40)
    await api.put(`/api/tenant/secrets/${longName}`, { data: { value: 'value-aaaa-1111' } })
    await api.put('/api/tenant/secrets/CRM_TOKEN', { data: { value: 'value-bbbb-2222' } })
    await api.post('/api/tenant/allowed-domains/', { data: { pattern: '*.' + 'sub'.repeat(20) + '.example.com' } })
    await api.post('/api/tenant/allowed-domains/', { data: { pattern: 'api.example.com' } })
    await page.setViewportSize({ width: 390, height: 844 })
    await openMemberSession(page, ADMIN_URL, tenant.token, ROUTE)
    await expect(page.getByText('CRM_TOKEN', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: `${longName} 값 바꾸기` }).click()
    const over = await page.evaluate(() => {
      const bad = []
      const root = document.documentElement
      if (root.scrollWidth > root.clientWidth) bad.push(`page ${root.scrollWidth}>${root.clientWidth}`)
      for (const el of document.querySelectorAll('body *')) {
        if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'visible') bad.push(`${el.tagName}.${el.className} ${el.scrollWidth}>${el.clientWidth}`)
      }
      const main = document.querySelector('main')
      if (main && main.scrollWidth > main.clientWidth) bad.push(`main ${main.scrollWidth}>${main.clientWidth}`)
      // 요소가 오른쪽 가장자리를 넘어 그려지는지도 본다
      for (const el of document.querySelectorAll('main *')) {
        const r = el.getBoundingClientRect()
        if (r.width && r.right > window.innerWidth + 1) bad.push(`right ${el.tagName} ${Math.round(r.right)}`)
      }
      return bad
    })
    expect(over).toEqual([])
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/e2e-390.png`, fullPage: true })
  })

  // 증거 사진 전용(SHOTS_DIR 이 있을 때만): 비밀 2개·도메인 2개 상태를 라이트·다크·390폭으로.
  test('증거 사진', async ({ page }) => {
    test.skip(!SHOTS, 'SHOTS_DIR 이 있을 때만')
    const tenant = await provisionTenant(`Secrets Shots ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    await api.put('/api/tenant/secrets/CRM_TOKEN', { data: { value: 'value-aaaa-1111' } })
    await api.put('/api/tenant/secrets/SLACK_WEBHOOK_URL', { data: { value: 'value-bbbb-2222' } })
    await api.post('/api/tenant/allowed-domains/', { data: { pattern: '*.example.com' } })
    await api.post('/api/tenant/allowed-domains/', { data: { pattern: 'api.crm.co.kr' } })
    await openMemberSession(page, ADMIN_URL, tenant.token, ROUTE)
    for (const [theme, w, h] of [['light', 1280, 900], ['dark', 1280, 900], ['light', 390, 844], ['dark', 390, 844]]) {
      await page.evaluate((t) => localStorage.setItem('admin_theme', t), theme)
      await page.setViewportSize({ width: w, height: h })
      await page.reload()
      await expect(page.getByText('CRM_TOKEN', { exact: true })).toBeVisible()
      await page.screenshot({ path: `${SHOTS}/secrets-${theme}-${w}.png`, fullPage: true })
    }
  })
})
