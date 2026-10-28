const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { settled, trackRequests } = require('./settle')

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
  test('목록에서 새 흐름 만들기 → 노드 추가(이어지지 않은 노드는 검증 오류)', async ({ page }) => {
    const net = trackRequests(page)
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
  })

  test('선 잇기 → 새로고침해도 노드·선이 남는다', async ({ page }) => {
    const tenant = await provisionTenant(`Flow Connect ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const graph = {
      nodes: [
        { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
        { id: 'ai_answer_1', kind: 'ai_answer', code: 'N-02', name: 'AI 답변', position: { x: 260, y: 40 }, config: { instructions: '임시' } },
        { id: 'guardrail_1', kind: 'guardrail', code: 'N-03', name: '가드레일', position: { x: 520, y: 40 }, config: { rules: ['banned_words'], banned_words: ['x'], action: 'block', target: 'output' } },
        { id: 'handoff_1', kind: 'handoff', code: 'N-04', name: '사람 넘김', position: { x: 780, y: 40 }, config: { reason: '필요' } },
      ],
      edges: [],
    }
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '선 잇기', graph } })).json()
    await api.dispose()
    await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
    await expect(page.getByLabel(/N-02 오류 \d건/)).toBeVisible({ timeout: 10000 }) // 이어지지 않은 노드는 검증 오류

    // 선 잇기: 시작 → AI 답변, AI 답변 → 가드레일 → 사람 넘김
    await connect(page, 'start', 'ai_answer_1')
    await expect(page.locator('.react-flow__edge')).toHaveCount(1)
    await connect(page, 'ai_answer_1', 'guardrail_1')
    await connect(page, 'guardrail_1', 'handoff_1')
    await expect(page.locator('.react-flow__edge')).toHaveCount(3)
    await expect(page.getByLabel(/N-02 오류/)).toHaveCount(0, { timeout: 10000 })

    // 새로고침해도 노드·선이 남는다
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
    await expect(page.locator('.react-flow__edge')).toHaveCount(3, { timeout: 15000 })
  })

  test('종류별 폼으로 설정 → 저장 → 새로고침해도 유지된다', async ({ page }) => {
    const net = trackRequests(page)
    // 노드·선은 화면이 아니라 API 로 만든다(#257, 3초 한도) — 위 시험이 화면 조작을 본다.
    const tenant = await provisionTenant(`Flow Forms ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const graph = {
      nodes: [
        { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
        { id: 'ai_answer_1', kind: 'ai_answer', code: 'N-02', name: 'AI 답변', position: { x: 260, y: 40 }, config: { instructions: '임시' } },
        { id: 'guardrail_1', kind: 'guardrail', code: 'N-03', name: '가드레일', position: { x: 520, y: 40 }, config: { rules: ['banned_words'], banned_words: ['x'], action: 'block', target: 'output' } },
        { id: 'handoff_1', kind: 'handoff', code: 'N-04', name: '사람 넘김', position: { x: 780, y: 40 }, config: { reason: '필요' } },
      ],
      edges: [{ from: 'start', to: 'ai_answer_1' }, { from: 'ai_answer_1', to: 'guardrail_1' }, { from: 'guardrail_1', to: 'handoff_1' }],
    }
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '환불 안내', graph } })).json()
    await api.dispose()
    await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
    await expect(page.locator('.react-flow__edge')).toHaveCount(3, { timeout: 15000 })

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
    await net.idle()
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
  })

  test('노드를 끌어 옮기면 위치가 초안에 저장된다', async ({ page }) => {
    // 화면 좌표는 새로고침 때 맞춤 보기로 바뀌므로 저장된 초안의 좌표로 본다. 흐름은 화면이 아니라 API 로 만든다(#257, 3초 한도).
    const tenant = await provisionTenant(`Flow Move ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const graph = {
      nodes: [
        { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
        { id: 'handoff_1', kind: 'handoff', code: 'N-02', name: '사람 넘김', position: { x: 260, y: 40 }, config: { reason: '필요' } },
      ],
      edges: [{ from: 'start', to: 'handoff_1' }],
    }
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '이동 확인', graph } })).json()
    await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
    await expect(page.locator('.react-flow__node')).toHaveCount(2)
    const flowId = made.id
    const posY = async () => {
      const d = await (await api.get(`/api/tenant/flows/${flowId}/`)).json()
      return d.draft.graph.nodes.find((n) => n.id === 'handoff_1').position.y
    }
    await settled(page) // 맞춤 보기가 끝난 뒤의 좌표로 끈다
    const y0 = await posY()
    const box = await page.locator('[data-node-id="handoff_1"]').boundingBox()
    await page.mouse.move(box.x + 60, box.y + 12)
    await page.mouse.down()
    await page.mouse.move(box.x + 60, box.y + 112, { steps: 8 })
    await page.mouse.up()
    await expect.poll(posY).toBeGreaterThan(y0 + 50)
    await api.dispose()
  })

  test('증거 사진: 예제 흐름·오류 램프·목록(라이트·다크·390)', async ({ page }) => {
    test.skip(!SHOTS, 'SHOTS_DIR 이 있을 때만')
    const tenant = await provisionTenant(`Flow Shots ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const editorUrl = `${ADMIN_URL}/admin-ui/tenant/flows/x`
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
    await settled(page)
    await shot(page, 'editor-light')
    await page.locator(`[data-node-id="${http.id}"]`).click()
    await settled(page)
    await shot(page, 'editor-error-light')
    await page.evaluate(() => localStorage.setItem('admin_theme', 'dark'))
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(graph.nodes.length)
    await page.locator('[data-node-id="guard"]').click()
    await settled(page)
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
    await settled(page)
    await shot(page, 'editor-390')
    await api.dispose()
  })
})
