const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { expectDocumentReady } = require('./documents')

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

  let contentDoc, toggleDoc

  // ready 문서 둘(청크 보기·청크 닫기)은 화면이 아니라 API 로 미리 만든다(#343) — 색인은 서버 비동기 작업이라
  // 시험 본문에서 ready 를 기다리면 느린 실행기에서 3초 한도를 넘었다. beforeAll 은 워커마다 돈다(fullyParallel).
  test.beforeAll(async () => {
    const ctx = await setup()
    tenant = ctx.tenant
    const api = await request.newContext({ baseURL: API_URL })
    const ts = Date.now()
    const upload = async (name, text) => {
      const res = await api.post('/api/tenant/documents/', {
        headers: { Authorization: `Bearer ${tenant.token}` },
        multipart: { file: { name, mimeType: 'text/plain', buffer: Buffer.from(text) } },
      })
      const id = (await res.json()).id
      return { id, name, text }
    }
    ;[contentDoc, toggleDoc] = await Promise.all([
      upload(`chunk-content-${ts}.txt`, 'Expression pedal A controls volume in FCB1010 presets.'),
      upload(`chunk-toggle-${ts}.txt`, `SYSEX-${ts} dump sends entire FCB1010 memory over MIDI.`),
    ])
    await Promise.all([contentDoc, toggleDoc].map((d) => expectDocumentReady(api, tenant.token, d.id)))
    await api.dispose()
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
    await loginAsTenantAgent(page, tenant)
    const docRow = page.locator('tr', { has: page.locator(`text=${contentDoc.name}`) })
    await expect(docRow).toBeVisible({ timeout: 10000 })
    await expect(docRow.locator('text=ready')).toBeVisible()

    await docRow.locator('button:has-text("청크 보기")').click()
    // 로딩 완료 후 청크 아이템이 나타날 때까지 대기
    await expect(page.locator('[data-testid="chunk-item"]').first()).toBeVisible({ timeout: 10000 })
    await expect(page.locator('[data-testid="chunk-content"]').first()).toContainText('Expression pedal')
  })

  // ── 토글 닫기 ─────────────────────────────────────────────────────────────
  test('"청크 닫기" 클릭 시 패널이 사라진다', async ({ page }) => {
    await loginAsTenantAgent(page, tenant)
    const docRow = page.locator('tr', { has: page.locator(`text=${toggleDoc.name}`) })
    await expect(docRow).toBeVisible({ timeout: 10000 })
    await expect(docRow.locator('text=ready')).toBeVisible()

    await docRow.locator('button:has-text("청크 보기")').click()
    await expect(page.locator('[data-testid="chunk-item"]').first()).toBeVisible({ timeout: 10000 })
    await expect(page.locator('[data-testid="chunk-content"]').first()).toContainText(toggleDoc.text.split(' ')[0])

    await docRow.locator('button:has-text("청크 닫기")').click()
    await expect(page.locator('[data-testid="chunk-item"]')).not.toBeVisible({ timeout: 3000 })
  })
})
