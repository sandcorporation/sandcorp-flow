const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'

async function setup() {
  const api = await request.newContext({ baseURL: API_URL })
  const tenant = await provisionTenant(`Chunk Inspector E2E ${Date.now()}`)
  await api.dispose()
  return { tenant }
}

async function loginAsTenantAgent(page, tenant) {
  await openMemberSession(page, ADMIN_URL, tenant.token)
  await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 10000 })
}

test.describe('Document Chunk Inspector', () => {
  let tenant

  test.beforeAll(async () => {
    const ctx = await setup()
    tenant = ctx.tenant
  })

  test.beforeEach(async ({ page }) => {
    await page.goto(`${ADMIN_URL}/admin-ui/`)
    await page.evaluate(() => localStorage.clear())
  })

  // ── Tracer Bullet ─────────────────────────────────────────────────────────
  test('"청크 보기" 버튼이 문서 행에 존재한다', async ({ page }) => {
    const api = await request.newContext({ baseURL: API_URL })
    const agentToken = tenant.token
    const ts = Date.now()
    await api.post('/api/tenant/documents/', {
      headers: { Authorization: `Bearer ${agentToken}` },
      multipart: {
        file: {
          name: `chunk-e2e-${ts}.txt`,
          mimeType: 'text/plain',
          buffer: Buffer.from('FCB1010 has ten footswitches for live performance use.'),
        },
      },
    })
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    const docRow = page.locator('tr', { has: page.locator(`text=chunk-e2e-${ts}.txt`) })
    await expect(docRow).toBeVisible({ timeout: 10000 })
    await expect(docRow.locator('button:has-text("청크 보기")')).toBeVisible()
  })

  // ── 청크 패널 열기 ────────────────────────────────────────────────────────
  test('ready 문서에서 "청크 보기" 클릭 시 청크 내용이 표시된다', async ({ page }) => {
    const api = await request.newContext({ baseURL: API_URL })
    const agentToken = tenant.token
    const ts = Date.now()
    await api.post('/api/tenant/documents/', {
      headers: { Authorization: `Bearer ${agentToken}` },
      multipart: {
        file: {
          name: `chunk-content-${ts}.txt`,
          mimeType: 'text/plain',
          buffer: Buffer.from('Expression pedal A controls volume in FCB1010 presets.'),
        },
      },
    })
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    const docRow = page.locator('tr', { has: page.locator(`text=chunk-content-${ts}.txt`) })
    await expect(docRow).toBeVisible({ timeout: 10000 })
    await expect(docRow.locator('text=ready')).toBeVisible({ timeout: 30000 })

    await docRow.locator('button:has-text("청크 보기")').click()
    // 로딩 완료 후 청크 아이템이 나타날 때까지 대기
    await expect(page.locator('[data-testid="chunk-item"]').first()).toBeVisible({ timeout: 10000 })
    await expect(page.locator('[data-testid="chunk-content"]').first()).toContainText('Expression pedal')
  })

  // ── 토글 닫기 ─────────────────────────────────────────────────────────────
  test('"청크 닫기" 클릭 시 패널이 사라진다', async ({ page }) => {
    const api = await request.newContext({ baseURL: API_URL })
    const agentToken = tenant.token
    const ts = Date.now()
    await api.post('/api/tenant/documents/', {
      headers: { Authorization: `Bearer ${agentToken}` },
      multipart: {
        file: {
          name: `chunk-toggle-${ts}.txt`,
          mimeType: 'text/plain',
          buffer: Buffer.from(`SYSEX-${ts} dump sends entire FCB1010 memory over MIDI.`),
        },
      },
    })
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    const docRow = page.locator('tr', { has: page.locator(`text=chunk-toggle-${ts}.txt`) })
    await expect(docRow).toBeVisible({ timeout: 10000 })
    await expect(docRow.locator('text=ready')).toBeVisible({ timeout: 30000 })

    await docRow.locator('button:has-text("청크 보기")').click()
    await expect(page.locator('[data-testid="chunk-item"]').first()).toBeVisible({ timeout: 10000 })
    await expect(page.locator('[data-testid="chunk-content"]').first()).toContainText(`SYSEX-${ts}`)

    await docRow.locator('button:has-text("청크 닫기")').click()
    await expect(page.locator('[data-testid="chunk-item"]')).not.toBeVisible({ timeout: 3000 })
  })
})
