const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { expectDocumentReady } = require('./documents')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'

async function setup() {
  const api = await request.newContext({ baseURL: API_URL })
  const tenant = await provisionTenant(`Document Label E2E ${Date.now()}`)
  await api.dispose()
  return { tenant }
}

async function agentToken(tenant) {
  return tenant.token
}

async function loginAsTenantAgent(page, tenant) {
  await openMemberSession(page, ADMIN_URL, tenant.token)
  await expect(page.locator('a:has-text("문서")')).toBeVisible({ timeout: 10000 })
}

test.describe('Document Label', () => {
  let tenant

  test.beforeAll(async () => {
    const ctx = await setup()
    tenant = ctx.tenant
  })

  // ── Tracer Bullet: 파일 선택 시 레이블이 파일명으로 미리 채워진다 ─────────────
  test('파일 선택 시 Document Label 입력이 파일명으로 미리 채워진다', async ({ page }) => {
    await loginAsTenantAgent(page, tenant)

    await page.setInputFiles('#file-upload', {
      name: 'ZX900PRO-manual.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('The unit offers ten assignable footswitches.'),
    })

    const labelInput = page.locator('[data-testid="upload-label-input"]')
    await expect(labelInput).toBeVisible({ timeout: 5000 })
    await expect(labelInput).toHaveValue('ZX900PRO-manual.txt')
  })

  // ── 모달에서 레이블을 바꿔 업로드하면 그 레이블로 문서가 생성된다 ───────────────
  test('모달에서 입력한 레이블이 업로드된 문서의 이름이 된다', async ({ page }) => {
    await loginAsTenantAgent(page, tenant)

    const ts = Date.now()
    await page.setInputFiles('#file-upload', {
      name: `rawfile-${ts}.txt`,
      mimeType: 'text/plain',
      buffer: Buffer.from('FCB1010 power supply requires 9V DC adapter.'),
    })

    const labelInput = page.locator('[data-testid="upload-label-input"]')
    await expect(labelInput).toBeVisible({ timeout: 5000 })
    await labelInput.fill(`FCB1010-${ts}`)
    await page.locator('[data-testid="upload-confirm"]').click()

    const docRow = page.locator('tr', { has: page.locator(`text=FCB1010-${ts}`) })
    await expect(docRow).toBeVisible({ timeout: 10000 })
    // 파일명이 아닌 레이블이 표시된다
    await expect(page.locator(`text=rawfile-${ts}.txt`)).toHaveCount(0)
  })

  // ── 목록에서 레이블을 수정하면 새 이름으로 갱신된다 ───────────────────────────
  test('목록에서 레이블을 수정하면 새 이름이 표시되고 재인덱싱된다', async ({ page }) => {
    const token = await agentToken(tenant)
    const ts = Date.now()
    const api = await request.newContext({ baseURL: API_URL })
    const upload = await api.post('/api/tenant/documents/', {
      headers: { Authorization: `Bearer ${token}` },
      multipart: {
        file: {
          name: `editme-${ts}.txt`,
          mimeType: 'text/plain',
          buffer: Buffer.from('Expression pedal calibration procedure for the device.'),
        },
      },
    })
    // 색인은 서버 비동기 작업이라 알릴 사건이 없다 — API 로 ready 를 다시 보고, 화면은 ready 인 채로 연다
    // (화면 목록은 3초 주기로 새로고침해 3초 한도 안에 ready 를 기다릴 수 없다, #257).
    const docId = (await upload.json()).id
    await expectDocumentReady(api, token, docId)
    await api.dispose()

    await loginAsTenantAgent(page, tenant)
    const docRow = page.locator('tr', { has: page.locator(`text=editme-${ts}.txt`) })
    await expect(docRow).toBeVisible({ timeout: 10000 })
    await expect(docRow.locator('text=ready')).toBeVisible()

    await docRow.locator('[data-testid="edit-label"]').click()
    // 편집 모드에서는 셀의 파일명 텍스트가 input으로 대체되므로 페이지 레벨로 찾는다
    // (한 번에 한 행만 편집되므로 input은 페이지에 하나뿐)
    const editInput = page.locator('[data-testid="edit-label-input"]')
    await expect(editInput).toBeVisible()
    await editInput.fill(`ZX900PRO-${ts}`)
    await page.locator('[data-testid="save-label"]').click()

    await expect(page.locator(`text=ZX900PRO-${ts}`)).toBeVisible({ timeout: 10000 })
  })
})
