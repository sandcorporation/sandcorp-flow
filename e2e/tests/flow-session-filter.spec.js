const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { settled } = require('./settle')

// 대화 목록·방문자 세션·사람 넘김의 흐름 칸과 필터(#249, 우산 #245 E4).
// 두 흐름의 대화는 화면이 아니라 API 로 만든다 — 임베드 주소로 방문자 한 명씩 말을 건다. 시험당 3초(#257).
const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'

const graph = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 60 }, config: {} },
    { id: 'gate', kind: 'guardrail', code: 'N-02', name: '입력 검사', position: { x: 260, y: 60 },
      config: { rules: ['banned_words'], banned_words: ['안녕'], action: 'block', target: 'input', block_message: '답했습니다' } },
  ],
  edges: [{ from: 'start', to: 'gate' }],
}

/** 기본 흐름 + 임베드를 켠 두 번째 흐름. 각 흐름에 방문자 한 명씩 세션을 만든다. */
async function prepare(name) {
  const tenant = await provisionTenant(`${name} ${Date.now()}`, 15000, { defaultFlow: true })
  const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
  const slug = `sf-${Date.now()}`
  expect((await api.patch('/api/tenant/slug/', { data: { slug } })).ok()).toBeTruthy()
  const flows = []
  for (const flowName of ['봄 이벤트', '환불 안내']) {
    const flow = await (await api.post('/api/tenant/flows/', { data: { name: flowName } })).json()
    expect((await api.put(`/api/tenant/flows/${flow.id}/draft/`, { data: { graph } })).ok()).toBeTruthy()
    const saved = await api.post(`/api/tenant/flows/${flow.id}/versions/`, { data: { note: 'v1' } })
    expect((await api.post(`/api/tenant/flows/${flow.id}/deploy/`, { data: { version: (await saved.json()).version.number } })).ok()).toBeTruthy()
    const on = await api.post(`/api/tenant/flows/${flow.id}/embed/`, { data: { enabled: true } })
    flows.push({ ...flow, key: (await on.json()).embed_key })
  }
  // 위젯 SSE 연결이 세션을 만든다 — 헤더가 오면 곧바로 끊는다.
  for (const [i, f] of flows.entries()) {
    const ctl = new AbortController()
    const r = await fetch(`${API_URL}/api/chat/stream?slug=${slug}&flow=${f.key}&visitor_id=sf-visitor-${i}`, { signal: ctl.signal })
    expect(r.status).toBe(200)
    ctl.abort()
  }
  return { tenant, api, flows }
}

test.describe('대화 목록의 흐름 칸·필터', () => {
  test('두 흐름의 대화가 구분되어 보이고 필터로 걸러진다', async ({ page }) => {
    const t = await prepare('flow-filter')
    await openMemberSession(page, ADMIN_URL, t.tenant.token, 'tenant/hitl')
    const names = page.getByTestId('flow-name')
    await expect(names).toHaveCount(2)
    await expect(names.filter({ hasText: '봄 이벤트' })).toHaveCount(1)
    await expect(names.filter({ hasText: '환불 안내' })).toHaveCount(1)

    await page.getByRole('combobox', { name: '흐름' }).selectOption({ label: '환불 안내' })
    await expect(names).toHaveCount(1)
    await expect(names.first()).toHaveText('환불 안내')
    await settled(page)
  })
})
