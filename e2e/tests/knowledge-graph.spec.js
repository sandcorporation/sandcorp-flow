const { test, expect, request } = require('@playwright/test')
const { operatorToken } = require('./operatorToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'

async function setup() {
  const api = await request.newContext({ baseURL: API_URL })
  const opToken = operatorToken()
  const tenantRes = await api.post('/api/operator/tenants/', {
    headers: { Authorization: `Bearer ${opToken}` },
    data: { name: `KG Inspector E2E ${Date.now()}` },
  })
  const tenant = await tenantRes.json()
  await api.dispose()
  return { tenant }
}

async function loginAsTenantAgent(page, tenant) {
  await page.goto(`${ADMIN_URL}/admin-ui/tenant`)
  await page.fill('input[placeholder="Tenant 이름"]', tenant.name)
  await page.fill('input[placeholder="사용자명"]', tenant.agent_username)
  await page.fill('input[placeholder="비밀번호"]', tenant.agent_temp_password)
  await page.click('button[type="submit"]')
  await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 10000 })
}

test.describe('Knowledge Graph 인스펙터', () => {
  let tenant

  test.beforeAll(async () => {
    const ctx = await setup()
    tenant = ctx.tenant
  })

  test.beforeEach(async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/`)
    await page.evaluate(() => localStorage.clear())
  })

  test('지식그래프 탭 진입 시 빈 안내가 보인다', async ({ page }) => {
    await loginAsTenantAgent(page, tenant)
    await page.goto(`${ADMIN_URL}/admin-ui/tenant/graph`)
    await expect(page.locator('[data-testid="kg-empty"]')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('[data-testid="kg-search"]')).toBeVisible()
  })

  test('엔티티 검색 → 노드 렌더 → 클릭 시 디테일 패널 표시', async ({ page }) => {
    // 문서 업로드 → Fake 추출이 레이블 Entity를 만든다
    const api = await request.newContext({ baseURL: API_URL })
    const agentRes = await api.post('/api/tenant/agents/auth/login', {
      data: {
        tenant_name: tenant.name,
        username: tenant.agent_username,
        password: tenant.agent_temp_password,
      },
    })
    const { access_token: agentToken } = await agentRes.json()
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
    for (let i = 0; i < 30; i++) {
      const docs = await (await api.get('/api/tenant/documents/', {
        headers: { Authorization: `Bearer ${agentToken}` },
      })).json()
      const doc = docs.find(d => d.id === docId)
      if (doc && doc.status === 'ready') break
      await new Promise(r => setTimeout(r, 1000))
    }
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    await page.goto(`${ADMIN_URL}/admin-ui/tenant/graph`)

    await page.fill('[data-testid="kg-search"]', `KGDOC-${ts}`)
    await page.keyboard.press('Enter')

    const node = page.locator('[data-testid="kg-node"]', { hasText: `KGDOC-${ts}.txt` })
    await expect(node).toBeVisible({ timeout: 10000 })

    await node.click()
    await expect(page.locator('[data-testid="kg-detail-name"]')).toContainText(`KGDOC-${ts}.txt`)
  })
})
