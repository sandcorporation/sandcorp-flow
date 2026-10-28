const { test, expect, request } = require('@playwright/test')
const { provisionTenant } = require('./memberToken')

// 한 테넌트의 두 흐름을 서로 다른 키 주소로 붙인다(#250, 우산 #245 E5 종합): 각자 다른 답을 하고, 대화(세션)는 따로이고,
// 기존 주소(/chatbot/<slug>/)는 기본 흐름이 답한다. 준비(조직·흐름·배포·켬)는 화면이 아니라 API 로 한다 — 시험당 3초(#257).
// 흐름은 막는 말(guardrail)의 고정 문구로 구분한다(Fake LLM 의 답은 늘 같다).
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SPRING = '봄 이벤트 흐름이 답했습니다'
const REFUND = '환불 안내 흐름이 답했습니다'

const graphSaying = (message) => ({
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 60 }, config: {} },
    { id: 'gate', kind: 'guardrail', code: 'N-02', name: '입력 검사', position: { x: 260, y: 60 },
      config: { rules: ['banned_words'], banned_words: ['안녕'], action: 'block', target: 'input', block_message: message } },
  ],
  edges: [{ from: 'start', to: 'gate' }],
})

async function deployedFlow(api, name, message) {
  const flow = await (await api.post('/api/tenant/flows/', { data: { name } })).json()
  expect((await api.put(`/api/tenant/flows/${flow.id}/draft/`, { data: { graph: graphSaying(message) } })).ok()).toBeTruthy()
  const saved = await api.post(`/api/tenant/flows/${flow.id}/versions/`, { data: { note: 'v1' } })
  expect((await api.post(`/api/tenant/flows/${flow.id}/deploy/`, { data: { version: (await saved.json()).version.number } })).ok()).toBeTruthy()
  const on = await api.post(`/api/tenant/flows/${flow.id}/embed/`, { data: { enabled: true } })
  expect(on.ok()).toBeTruthy()
  return { ...flow, key: (await on.json()).embed_key }
}

/** 기본 흐름이 있는 조직 + 임베드를 켠 「봄 이벤트」·「환불 안내」 흐름. */
async function prepare(name) {
  const tenant = await provisionTenant(`${name} ${Date.now()}`, 15000, { defaultFlow: true })
  const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
  const slug = `ov-${Date.now()}`
  expect((await api.patch('/api/tenant/slug/', { data: { slug } })).ok()).toBeTruthy()
  const spring = await deployedFlow(api, '봄 이벤트', SPRING)
  const refund = await deployedFlow(api, '환불 안내', REFUND)
  return { api, slug, spring, refund }
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

/** 위젯 SSE 연결이 세션을 만든다 — 헤더가 오면 곧바로 끊는다. */
async function openSession(slug, key, visitor) {
  const ctl = new AbortController()
  const flow = key ? `&flow=${key}` : ''
  const r = await fetch(`${API_URL}/api/chat/stream?slug=${slug}${flow}&visitor_id=${visitor}`, { signal: ctl.signal })
  expect(r.status).toBe(200)
  ctl.abort()
}

test.describe('한 테넌트의 두 흐름을 서로 다른 주소로', () => {
  test('두 키 주소가 각자 자기 흐름으로 답한다', async ({ browser }) => {
    const t = await prepare('Overall Answers')
    const [a, b] = await Promise.all([
      visitorSays(browser, `${WIDGET_URL}/embed/chatbot/${t.slug}/${t.spring.key}/?visitor_id=ov-answer-a`),
      visitorSays(browser, `${WIDGET_URL}/embed/chatbot/${t.slug}/${t.refund.key}/?visitor_id=ov-answer-b`),
    ])
    expect(a).toContain(SPRING)
    expect(a).not.toContain(REFUND)
    expect(b).toContain(REFUND)
    expect(b).not.toContain(SPRING)
    await t.api.dispose()
  })

  test('같은 방문자도 흐름마다 대화(세션)가 따로다', async () => {
    const t = await prepare('Overall Sessions')
    await openSession(t.slug, t.spring.key, 'ov-same-visitor')
    await openSession(t.slug, t.refund.key, 'ov-same-visitor')
    const rows = await (await t.api.get('/api/tenant/visitors/ov-same-visitor/sessions/')).json()
    expect(rows).toHaveLength(2)
    expect(new Set(rows.map((r) => r.session_id)).size).toBe(2)
    expect(rows.map((r) => r.flow_name).sort()).toEqual(['봄 이벤트', '환불 안내'])
    await t.api.dispose()
  })

  test('기존 주소는 기본 흐름이 답한다', async ({ browser }) => {
    const t = await prepare('Overall Default')
    expect((await t.api.post(`/api/tenant/flows/${t.spring.id}/default/`)).ok()).toBeTruthy()
    const legacy = await visitorSays(browser, `${WIDGET_URL}/embed/chatbot/${t.slug}/?visitor_id=ov-default`)
    expect(legacy).toContain(SPRING) // 기존 주소 = 기본 흐름(다른 키 주소와 답이 다르다)
    expect(legacy).not.toContain(REFUND)
    await t.api.dispose()
  })
})
