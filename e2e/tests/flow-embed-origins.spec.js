const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

// 허용 사이트(임베드 출처) 목록과 동적 frame-ancestors(#248, ADR-0034): 운영 nginx 설정(nginx-e2e)이 auth_request 로 앱에 묻는다.
// 앞 단계(조직·흐름·배포·목록)는 API 로 한다 — 시험당 3초(#257). 헤더는 nginx-e2e 를 거친 응답에서 본다.
// iframe 막힘은 두 출처로 본다: 'self'(nginx-e2e 자신의 페이지)는 뜨고, 다른 출처(api-e2e 의 /api/health)에서는 막힌다.
// (허용 목록에 admin-e2e 를 넣는 쪽은 못 한다 — http 는 localhost 만 받는다. 목록 값이 헤더에 나오는지는 헤더 검사로 본다.)
const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const NGINX_URL = process.env.NGINX_URL || 'http://localhost:8080'

const graph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 60 }, config: {} },
    { id: 'gate', kind: 'guardrail', code: 'N-02', name: '입력 검사', position: { x: 260, y: 60 },
      config: { rules: ['banned_words'], banned_words: ['안녕'], action: 'block', target: 'input', block_message: '허용 사이트 시험' } },
  ],
  edges: [{ from: 'start', to: 'gate' }],
}

/** 기본 흐름이 있는 조직 + 배포하고 임베드를 켠 「봄 이벤트」 흐름. */
async function prepare(name) {
  const tenant = await provisionTenant(`${name} ${Date.now()}`, 15000, { defaultFlow: true })
  const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
  const slug = `fo-${Date.now()}-${Math.floor(Math.random() * 1000)}`
  expect((await api.patch('/api/tenant/slug/', { data: { slug } })).ok()).toBeTruthy()
  const flow = await (await api.post('/api/tenant/flows/', { data: { name: '봄 이벤트' } })).json()
  expect((await api.put(`/api/tenant/flows/${flow.id}/draft/`, { data: { graph } })).ok()).toBeTruthy()
  const saved = await api.post(`/api/tenant/flows/${flow.id}/versions/`, { data: { note: 'v1' } })
  expect((await api.post(`/api/tenant/flows/${flow.id}/deploy/`, { data: { version: (await saved.json()).version.number } })).ok()).toBeTruthy()
  const on = await api.post(`/api/tenant/flows/${flow.id}/embed/`, { data: { enabled: true } })
  const key = (await on.json()).embed_key
  return { tenant, api, slug, flow, defaultPath: `/chatbot/${slug}/`, keyPath: `/chatbot/${slug}/${key}/` }
}

async function csp(path) {
  const ctx = await request.newContext({ baseURL: NGINX_URL })
  const r = await ctx.get(path)
  expect(r.status()).toBe(200)
  const value = r.headers()['content-security-policy']
  await ctx.dispose()
  return value
}

/** pageUrl 을 열고 그 안에 src 를 iframe 으로 붙인다. 막히면 Chromium 은 프레임을 오류 페이지로 바꾼다. */
async function frameFrom(page, pageUrl, src) {
  await page.goto(pageUrl)
  await page.evaluate((s) => new Promise((resolve) => {
    const f = document.createElement('iframe')
    f.name = 'w'
    f.onload = () => resolve()
    f.src = s
    document.body.appendChild(f)
  }), src)
  return page.frame({ name: 'w' })
}

test.describe('허용 사이트와 frame-ancestors(운영 nginx 경유)', () => {
  test('목록이 비면 기본 주소와 키 주소 모두 frame-ancestors *', async () => {
    const t = await prepare('Origins Empty')
    expect(await csp(t.defaultPath)).toBe('frame-ancestors *')
    expect(await csp(t.keyPath)).toBe('frame-ancestors *')
    await t.api.dispose()
  })

  test('테넌트 목록이 두 주소에 나오고, 흐름 목록은 그 흐름의 키 주소에서만 대신한다', async () => {
    const t = await prepare('Origins Lists')
    expect((await t.api.patch('/api/tenant/config/', { data: { embed_origins: ['https://shop.example.com'] } })).ok()).toBeTruthy()
    expect(await csp(t.defaultPath)).toBe("frame-ancestors 'self' https://shop.example.com")
    expect(await csp(t.keyPath)).toBe("frame-ancestors 'self' https://shop.example.com")
    const put = await t.api.put(`/api/tenant/flows/${t.flow.id}/embed-origins/`, { data: { origins: ['https://*.campaign.example.net'] } })
    expect(put.ok()).toBeTruthy()
    expect(await csp(t.keyPath)).toBe("frame-ancestors 'self' https://*.campaign.example.net")
    expect(await csp(t.defaultPath)).toBe("frame-ancestors 'self' https://shop.example.com")
    await t.api.dispose()
  })

  test('목록이 비면 다른 출처에서도 iframe 이 뜬다', async ({ page }) => {
    const t = await prepare('Origins Frame Open')
    const frame = await frameFrom(page, `${API_URL}/api/health`, `${NGINX_URL}${t.defaultPath}`)
    await expect(frame.locator('#w')).toHaveText('nginx-widget')
    await t.api.dispose()
  })

  test('목록을 넣으면 허용하지 않은 출처의 iframe 은 막히고 같은 출처는 뜬다', async ({ page }) => {
    const t = await prepare('Origins Frame Block')
    expect((await t.api.patch('/api/tenant/config/', { data: { embed_origins: ['https://shop.example.com'] } })).ok()).toBeTruthy()
    const blocked = await frameFrom(page, `${API_URL}/api/health`, `${NGINX_URL}${t.defaultPath}`)
    expect(blocked.url()).toMatch(/^chrome-error:/)
    const own = await frameFrom(page, `${NGINX_URL}/embed/`, `${NGINX_URL}${t.defaultPath}`)
    await expect(own.locator('#w')).toHaveText('nginx-widget')
    await t.api.dispose()
  })

  test('Admin이 흐름 「임베드」 패널에서 허용 사이트가 틀리면 서버 문구를 보인다', async ({ page }) => {
    const t = await prepare('Origins Panel')
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/flows')
    await page.getByRole('button', { name: '봄 이벤트 임베드' }).click()
    const panel = page.getByRole('region', { name: '봄 이벤트 임베드' })
    await expect(panel.getByText(/테넌트 목록을 따름/)).toBeVisible()
    const box = panel.getByRole('textbox', { name: '허용 사이트' })
    await box.fill('http://example.com')
    await panel.getByRole('button', { name: '허용 사이트 저장' }).click()
    await expect(panel.getByRole('alert')).toContainText('http는 localhost·127.0.0.1에만 쓸 수 있습니다')
    await t.api.dispose()
  })

  test('Admin이 흐름 「임베드」 패널에서 맞는 허용 사이트를 저장하면 키 주소 헤더에 반영된다', async ({ page }) => {
    const t = await prepare('Origins Panel Ok')
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/flows')
    await page.getByRole('button', { name: '봄 이벤트 임베드' }).click()
    const panel = page.getByRole('region', { name: '봄 이벤트 임베드' })
    const box = panel.getByRole('textbox', { name: '허용 사이트' })
    await box.fill('https://Campaign.example.net')
    await panel.getByRole('button', { name: '허용 사이트 저장' }).click()
    await expect(panel.getByRole('button', { name: '✓ 저장됨' })).toBeVisible()
    await expect(box).toHaveValue('https://campaign.example.net')
    expect(await csp(t.keyPath)).toBe("frame-ancestors 'self' https://campaign.example.net")
    await t.api.dispose()
  })

  test('Admin이 보안 카드에서 테넌트 목록을 저장하면 기본 주소 헤더에 반영된다', async ({ page }) => {
    const t = await prepare('Origins Card')
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/config?section=security')
    const card = page.locator('section[aria-label="내 사이트에 붙이기"]')
    await expect(card.getByText(/비어 있으면 어디서나 붙일 수 있습니다/)).toBeVisible()
    await card.getByRole('textbox', { name: '허용 사이트' }).fill('https://sandcorp.co.kr\nhttps://www.sandcorp.co.kr')
    await card.getByRole('button', { name: '허용 사이트 저장' }).click()
    await expect(card.getByRole('button', { name: '✓ 저장됨' })).toBeVisible()
    expect(await csp(t.defaultPath)).toBe("frame-ancestors 'self' https://sandcorp.co.kr https://www.sandcorp.co.kr")
    await t.api.dispose()
  })
})
