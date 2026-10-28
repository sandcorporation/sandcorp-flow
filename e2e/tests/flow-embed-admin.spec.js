const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { settled } = require('./settle')

// 흐름별 임베드 관리(#247, ADR-0034): admin 흐름 목록의 「임베드」 패널로 켜고, 코드를 복사하고, 기본 흐름을 바꾼다.
// 앞 단계(조직·흐름 만들기·배포)는 화면이 아니라 API 로 한다 — 시험당 3초(#257). 흐름은 막는 말(guardrail)의 고정 문구로 구분한다(Fake LLM 의 답은 늘 같다).
const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SPRING = '봄 이벤트 흐름이 답했습니다'

const graphSaying = (message) => ({
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 60 }, config: {} },
    { id: 'gate', kind: 'guardrail', code: 'N-02', name: '입력 검사', position: { x: 260, y: 60 },
      config: { rules: ['banned_words'], banned_words: ['안녕'], action: 'block', target: 'input', block_message: message } },
  ],
  edges: [{ from: 'start', to: 'gate' }],
})

/** 기본 흐름이 있는 조직 + 배포한 「봄 이벤트」 흐름(임베드는 꺼 둠). */
async function prepare(name, { deploy = true } = {}) {
  const tenant = await provisionTenant(`${name} ${Date.now()}`, 15000, { defaultFlow: true })
  const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
  const slug = `fe-${Date.now()}`
  expect((await api.patch('/api/tenant/slug/', { data: { slug } })).ok()).toBeTruthy()
  const flow = await (await api.post('/api/tenant/flows/', { data: { name: '봄 이벤트' } })).json()
  expect((await api.put(`/api/tenant/flows/${flow.id}/draft/`, { data: { graph: graphSaying(SPRING) } })).ok()).toBeTruthy()
  if (deploy) {
    const saved = await api.post(`/api/tenant/flows/${flow.id}/versions/`, { data: { note: 'v1' } })
    expect(saved.ok()).toBeTruthy()
    expect((await api.post(`/api/tenant/flows/${flow.id}/deploy/`, { data: { version: (await saved.json()).version.number } })).ok()).toBeTruthy()
  }
  return { tenant, api, slug, flow }
}

async function stubClipboard(page) {
  // 시험 스택의 admin 은 http://admin-e2e(비보안 컨텍스트)라 navigator.clipboard 가 없다 — 앱이 writeText 에 넘기는 값을 가로챈다.
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (t) => { window.__copied = t } }, configurable: true })
  })
}

async function visitorSays(browser, url) {
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  await page.goto(url)
  const box = page.locator('textarea[placeholder="메시지를 입력하세요..."]')
  await expect(box).toBeEnabled()
  await box.fill('안녕하세요')
  await page.click('button:has-text("전송")')
  const reply = page.locator('[data-role="assistant"]').last()
  await expect(reply).toBeVisible()
  const text = await reply.innerText()
  await ctx.close()
  return text
}

test.describe('흐름 임베드 관리', () => {
  test('Admin이 임베드를 켜고 코드를 복사한다', async ({ page }) => {
    const t = await prepare('Embed Admin')
    await stubClipboard(page)
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/flows')
    await page.getByRole('button', { name: '봄 이벤트 임베드' }).click()
    await page.getByRole('checkbox', { name: '임베드 켜기' }).click()
    await expect(page.getByTestId('flow-embed-code')).toContainText(`/chatbot/${t.slug}/`)
    await page.getByRole('button', { name: '코드 복사' }).click()
    const code = await page.evaluate(() => window.__copied)
    expect(code).toMatch(new RegExp(`^<iframe src="[^"]+/chatbot/${t.slug}/[a-z0-9]{8}/"`))
    const row = (await (await t.api.get('/api/tenant/flows/')).json()).find((f) => f.id === t.flow.id)
    expect(row.embed_enabled).toBe(true)
    expect(code).toContain(row.embed_key)
    await t.api.dispose()
  })

  test('켠 키 주소로 위젯이 그 흐름으로 답한다', async ({ browser }) => {
    const t = await prepare('Embed Answer')
    const on = await t.api.post(`/api/tenant/flows/${t.flow.id}/embed/`, { data: { enabled: true } })
    expect(on.ok()).toBeTruthy()
    const key = (await on.json()).embed_key
    const text = await visitorSays(browser, `${WIDGET_URL}/embed/chatbot/${t.slug}/${key}/?visitor_id=e2e-embed-admin`)
    expect(text).toContain(SPRING)
    await t.api.dispose()
  })

  test('Admin이 「기본 흐름으로」를 누르면 그 흐름이 기본 흐름이 된다', async ({ page }) => {
    const t = await prepare('Embed Default UI')
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/flows')
    await page.getByRole('button', { name: '봄 이벤트 임베드' }).click()
    await page.getByRole('button', { name: '기본 흐름으로' }).click()
    await expect(page.getByRole('button', { name: '기본 흐름으로' })).toHaveCount(0)
    const row = (await (await t.api.get('/api/tenant/flows/')).json()).find((f) => f.id === t.flow.id)
    expect(row.is_public).toBe(true)
    await t.api.dispose()
  })

  test('기본 흐름을 바꾸면 기존 주소가 새 흐름으로 답한다(새 세션)', async ({ browser }) => {
    const t = await prepare('Embed Default')
    expect((await t.api.post(`/api/tenant/flows/${t.flow.id}/default/`)).ok()).toBeTruthy()
    const after = await visitorSays(browser, `${WIDGET_URL}/embed/chatbot/${t.slug}/?visitor_id=e2e-default-after`)
    expect(after).toContain(SPRING)
    await t.api.dispose()
  })

  test('배포 전 흐름은 켤 수 없고 이유가 보인다', async ({ page }) => {
    const t = await prepare('Embed Undeployed', { deploy: false })
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/flows')
    await page.getByRole('button', { name: '봄 이벤트 임베드' }).click()
    await expect(page.getByRole('checkbox', { name: '임베드 켜기' })).toBeDisabled()
    await expect(page.getByText(/배포한 버전이 없어 켤 수 없습니다/)).toBeVisible()
    await t.api.dispose()
  })

  test('설정 > 보안 카드가 켠 흐름과 흐름 목록 링크를 보인다', async ({ page }) => {
    const t = await prepare('Embed Card')
    expect((await t.api.post(`/api/tenant/flows/${t.flow.id}/embed/`, { data: { enabled: true } })).ok()).toBeTruthy()
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/config?section=security')
    const card = page.getByRole('region', { name: '내 사이트에 붙이기' })
    await expect(card.getByText('봄 이벤트')).toBeVisible()
    await expect(card.getByRole('link', { name: /흐름 목록으로/ })).toBeVisible()
    await t.api.dispose()
  })

  test('설정 > 보안 카드의 코드 복사가 그 흐름의 주소를 복사한다', async ({ page }) => {
    const t = await prepare('Embed Card Copy')
    expect((await t.api.post(`/api/tenant/flows/${t.flow.id}/embed/`, { data: { enabled: true } })).ok()).toBeTruthy()
    await stubClipboard(page)
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/config?section=security')
    await page.getByRole('region', { name: '내 사이트에 붙이기' }).getByRole('button', { name: '봄 이벤트 코드 복사' }).click()
    expect(await page.evaluate(() => window.__copied)).toContain(`/chatbot/${t.slug}/`)
    await t.api.dispose()
  })
})

test.describe('폰 폭(390) 임베드 화면', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  async function noOverflow(page) {
    await settled(page)
    return page.evaluate(() => {
      const de = document.documentElement
      const wide = [...document.querySelectorAll('section, pre, table, main')].filter((el) => {
        const r = el.getBoundingClientRect()
        return r.width > 0 && r.right > de.clientWidth + 1
      }).map((el) => el.tagName + (el.getAttribute('aria-label') ? `[${el.getAttribute('aria-label')}]` : ''))
      return { page: de.scrollWidth > de.clientWidth, wide }
    })
  }

  async function preparedPhone(name) {
    const t = await prepare(name)
    expect((await t.api.post(`/api/tenant/flows/${t.flow.id}/embed/`, { data: { enabled: true } })).ok()).toBeTruthy()
    return t
  }

  test('흐름 목록의 임베드 패널이 넘치지 않는다', async ({ page }) => {
    const t = await preparedPhone('Embed Phone List')
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/flows')
    await page.getByRole('button', { name: '봄 이벤트 임베드' }).click()
    await expect(page.getByTestId('flow-embed-code')).toBeVisible()
    expect(await noOverflow(page)).toEqual({ page: false, wide: [] })
    await t.api.dispose()
  })

  test('편집기의 임베드 메뉴가 넘치지 않는다', async ({ page }) => {
    const t = await preparedPhone('Embed Phone Editor')
    await openMemberSession(page, ADMIN_URL, t.tenant.token, `tenant/flows/${t.flow.id}`)
    await page.getByRole('button', { name: '임베드 메뉴' }).click()
    await expect(page.getByTestId('flow-embed-code')).toBeVisible()
    expect(await noOverflow(page)).toEqual({ page: false, wide: [] })
    await t.api.dispose()
  })

  test('설정 > 보안 카드가 넘치지 않는다', async ({ page }) => {
    const t = await preparedPhone('Embed Phone Card')
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/config?section=security')
    await expect(page.getByRole('region', { name: '내 사이트에 붙이기' }).getByText('봄 이벤트')).toBeVisible()
    expect(await noOverflow(page)).toEqual({ page: false, wide: [] })
    await t.api.dispose()
  })
})
