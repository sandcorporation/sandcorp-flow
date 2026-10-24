const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR // 있으면 증거 스크린샷을 남긴다
const TAG = process.env.SHOT_TAG || 'after'

async function shot(page, name) {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${TAG}-${name}.png`) })
}

// 끌어서 잇는다: 시작 노드의 나가는 점 → AI 답변 노드의 들어오는 점.
async function connect(page, from, to) {
  const a = await page.locator(`.react-flow__handle.source[data-nodeid="${from}"]`).first().boundingBox()
  const b = await page.locator(`.react-flow__handle.target[data-nodeid="${to}"]`).first().boundingBox()
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 })
  await page.mouse.up()
}

test.describe('흐름 편집기 — 실제 스택', () => {
  test('노드 추가 → 선 잇기 → 설정 저장 → 새로고침해도 유지된다', async ({ page }) => {
    test.setTimeout(90_000)
    page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) console.log('[console]', m.type(), m.text().slice(0, 300)) })
    const tenant = await provisionTenant(`Flow Editor ${Date.now()}`)
    await openMemberSession(page, ADMIN_URL, tenant.token, 'tenant/flows')

    // 목록 → 새 흐름 → 편집기
    await expect(page.getByLabel('새 흐름 이름')).toBeVisible({ timeout: 10000 })
    await shot(page, 'list-empty')
    await page.getByLabel('새 흐름 이름').fill('환불 안내')
    await page.getByRole('button', { name: '만들기' }).click()
    await expect(page.getByRole('heading', { name: '환불 안내' })).toBeVisible()
    await page.waitForURL(/\/tenant\/flows\/[0-9a-f-]{36}$/)
    const editorUrl = page.url()

    // 노드 추가(시작 → AI 답변 → 사람 넘김)
    await page.getByRole('button', { name: '시작', exact: true }).click()
    await page.getByRole('button', { name: 'AI 답변', exact: true }).click()
    await page.getByRole('button', { name: '사람 넘김', exact: true }).click()
    await page.getByRole('button', { name: '가드레일', exact: true }).click()
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
    await expect(page.getByText('N-02', { exact: true })).toBeVisible()
    // 시작 외에 이어지지 않은 노드는 검증 오류로 표시된다
    await expect(page.getByLabel(/N-02 오류 \d건/)).toBeVisible({ timeout: 10000 })

    // 선 잇기: 시작 → AI 답변, AI 답변 → 가드레일 → 사람 넘김
    await connect(page, 'start', 'ai_answer_1')
    await expect(page.locator('.react-flow__edge')).toHaveCount(1)
    await connect(page, 'ai_answer_1', 'guardrail_1')
    await connect(page, 'guardrail_1', 'handoff_1')
    await expect(page.locator('.react-flow__edge')).toHaveCount(3)
    await expect(page.getByLabel(/N-02 오류/)).toHaveCount(0, { timeout: 10000 })

    // 종류별 폼으로 설정: AI 답변 지침, 가드레일(처리=가리기 + 금지어 규칙·목록)
    await page.locator('[data-node-id="ai_answer_1"]').click()
    await page.getByLabel('답변 지침').fill('환불 규정을 안내한다')
    await page.getByLabel('답변 지침').blur()
    await page.locator('[data-node-id="guardrail_1"]').click()
    await expect(page.getByText(/나가는 말을 바꾸거나 막을 수 있습니다/)).toBeVisible()
    await page.getByLabel('처리').selectOption('mask')
    await page.getByRole('checkbox', { name: '금지어' }).check()
    await page.getByLabel('금지어 목록').fill('환불 보장\n공짜')
    await page.getByLabel('금지어 목록').blur()
    await expect(page.getByRole('status').filter({ hasText: '초안 저장됨' })).toBeVisible()
    await page.waitForTimeout(500)
    await expect(page.getByRole('status').filter({ hasText: '초안 저장됨' })).toBeVisible()
    await shot(page, 'editor-light')

    // 새로고침해도 노드·선·설정이 남는다
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
    await expect(page.locator('.react-flow__edge')).toHaveCount(3, { timeout: 15000 })
    await expect(page.getByText('환불 규정을 안내한다')).toBeVisible()
    await page.locator('[data-node-id="guardrail_1"]').click()
    await expect(page.getByLabel('처리')).toHaveValue('mask')
    await expect(page.getByRole('checkbox', { name: '금지어' })).toBeChecked()
    await expect(page.getByLabel('금지어 목록')).toHaveValue('환불 보장\n공짜')
    await expect(page.getByText('검증 통과')).toBeVisible()

    // 위치 이동도 저장된다(화면 좌표는 새로고침 때 맞춤 보기로 바뀌므로 저장된 초안의 좌표로 본다)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const flowId = editorUrl.split('/').pop()
    const posY = async () => {
      const d = await (await api.get(`/api/tenant/flows/${flowId}/`)).json()
      return d.draft.graph.nodes.find((n) => n.id === 'handoff_1').position.y
    }
    const y0 = await posY()
    const box = await page.locator('[data-node-id="handoff_1"]').boundingBox()
    await page.mouse.move(box.x + 60, box.y + 12)
    await page.mouse.down()
    await page.mouse.move(box.x + 60, box.y + 112, { steps: 8 })
    await page.mouse.up()
    await expect.poll(posY).toBeGreaterThan(y0 + 50)

    if (SHOTS) {
      // 증거용: 예제 흐름(홈페이지 상담)을 그대로 올리고, HTTP 도구 한 곳을 일부러 틀려 오류 램프를 보인다.
      const graph = JSON.parse(fs.readFileSync(path.join(__dirname, 'flow-showcase.json'), 'utf8'))
      const http = graph.nodes.find((n) => n.kind === 'http_tool')
      http.config.url = 'http://insecure.example.com/inquiries'
      const made = await (await api.post('/api/tenant/flows/', { data: { name: '홈페이지 상담', graph } })).json()
      const showUrl = editorUrl.replace(/flows\/.*/, `flows/${made.id}`)
      await page.setViewportSize({ width: 1600, height: 900 })
      await page.goto(showUrl)
      await expect(page.locator('.react-flow__node')).toHaveCount(graph.nodes.length)
      await page.locator('[data-node-id="guard"]').click()
      await page.waitForTimeout(400)
      await shot(page, 'editor-light')
      await page.locator(`[data-node-id="${http.id}"]`).click()
      await page.waitForTimeout(300)
      await shot(page, 'editor-error-light')
      await page.evaluate(() => localStorage.setItem('admin_theme', 'dark'))
      await page.reload()
      await expect(page.locator('.react-flow__node')).toHaveCount(graph.nodes.length)
      await page.locator('[data-node-id="guard"]').click()
      await page.waitForTimeout(400)
      await shot(page, 'editor-dark')
      await page.goto(editorUrl.replace(/flows\/.*/, 'flows'))
      await expect(page.getByRole('link', { name: '홈페이지 상담' })).toBeVisible()
      await shot(page, 'list-dark')
      await page.evaluate(() => localStorage.setItem('admin_theme', 'light'))
      await page.reload()
      await expect(page.getByRole('link', { name: '홈페이지 상담' })).toBeVisible()
      await shot(page, 'list-light')
      await page.setViewportSize({ width: 390, height: 844 })
      await page.reload()
      await expect(page.getByRole('link', { name: '홈페이지 상담' })).toBeVisible()
      await shot(page, 'list-390')
      await page.goto(showUrl)
      await expect(page.locator('.react-flow__node').first()).toBeVisible()
      await page.waitForTimeout(500)
      await shot(page, 'editor-390')
    }
    await api.dispose()
  })
})
