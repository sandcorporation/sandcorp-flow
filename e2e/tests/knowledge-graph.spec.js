const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'

async function setup() {
  const api = await request.newContext({ baseURL: API_URL })
  const tenant = await provisionTenant(`KG Inspector E2E ${Date.now()}`)
  await api.dispose()
  return { tenant }
}

test.describe('Knowledge Graph 인스펙터', () => {
  let tenant

  test.beforeAll(async () => {
    const ctx = await setup()
    tenant = ctx.tenant
  })

  test('지식그래프 탭 진입 시 빈 안내가 보인다', async ({ page }) => {
    await openMemberSession(page, ADMIN_URL, tenant.token, 'tenant/graph') // 첫 화면을 거치지 않고 곧바로(#257)
    await expect(page.locator('[data-testid="kg-empty"]')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('[data-testid="kg-search"]')).toBeVisible()
  })

  test('엔티티 검색 → 노드 렌더 → 클릭 시 디테일 패널 표시', async ({ page }) => {
    // 문서 업로드 → Fake 추출이 레이블 Entity를 만든다
    const api = await request.newContext({ baseURL: API_URL })
    const agentToken = tenant.token
    const ts = Date.now()
    const upRes = await api.post('/api/tenant/documents/', {
      headers: { Authorization: `Bearer ${agentToken}` },
      multipart: {
        file: {
          name: `KGDOC-${ts}.txt`,
          mimeType: 'text/plain',
          buffer: Buffer.from('footswitch and expression pedal spec'),
        },
      },
    })
    const docId = (await upRes.json()).id

    // 비동기 그래프 인제스션이 끝날 때까지(status=ready) 대기
    await expect.poll(async () => {
      const docs = await (await api.get('/api/tenant/documents/', {
        headers: { Authorization: `Bearer ${agentToken}` },
      })).json()
      return docs.find(d => d.id === docId)?.status
    }, { timeout: 60_000, intervals: [100] }).toBe('ready') // 서버 비동기 작업이라 알릴 사건이 없다 — 상태를 다시 본다
    await api.dispose()

    await openMemberSession(page, ADMIN_URL, tenant.token, 'tenant/graph') // 첫 화면을 거치지 않고 곧바로(#257)

    await page.fill('[data-testid="kg-search"]', `KGDOC-${ts}`)
    await page.keyboard.press('Enter')

    const node = page.locator('[data-testid="kg-node"]', { hasText: `KGDOC-${ts}.txt` })
    await expect(node).toBeVisible({ timeout: 10000 })

    await node.click()
    await expect(page.locator('[data-testid="kg-detail-name"]')).toContainText(`KGDOC-${ts}.txt`)
  })
})
