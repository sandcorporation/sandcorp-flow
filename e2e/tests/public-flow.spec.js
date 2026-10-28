// 공개 흐름 보기(#39) — 실제 스택. 조직을 만들어 공개 흐름을 예제 ① 그대로 배포하고 「공개 흐름 보기」를 켠 뒤,
// 로그인 없는 브라우저로 /admin-ui/flows/{slug} 를 연다(라이트·다크·390폭·데스크톱). 꺼진 조직은 없는 slug 와 같은 404.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')
const { provisionTenant } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${process.env.SHOT_TAG || 'after'}-${name}.png`) })
}

/** 가로 넘침 없음 — 문서와 시트·하단 탭·캔버스를 scrollWidth 로 잰다. */
const overflow = (page) => page.evaluate(() => {
  const bad = []
  const de = document.documentElement
  if (de.scrollWidth > de.clientWidth) bad.push(`html ${de.scrollWidth}>${de.clientWidth}`)
  for (const sel of ['header', '[data-testid="phone-sheet"]', 'nav[aria-label="하단 탭"]', '[data-testid="flow-canvas"]']) {
    for (const el of document.querySelectorAll(sel)) if (el.scrollWidth > el.clientWidth) bad.push(`${sel} ${el.scrollWidth}>${el.clientWidth}`)
  }
  return bad
})

const slugOf = () => `pub-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`

/** 조직 하나 + slug, 공개 흐름을 예제 ① 로 배포. publicView 면 「공개 흐름 보기」를 켠다. */
async function publicTenant(request, { publicView }) {
  const { token } = await provisionTenant(`공개보기 ${Date.now()}`, 15000, { defaultFlow: true })
  const auth = { Authorization: `Bearer ${token}` }
  const slug = slugOf()
  expect((await request.patch(`${API_URL}/api/tenant/slug/`, { headers: auth, data: { slug } })).ok()).toBe(true)
  const flows = await (await request.get(`${API_URL}/api/tenant/flows/`, { headers: auth })).json()
  const flow = flows.find((f) => f.is_public)
  const graph = (await (await request.get(`${API_URL}/api/try/templates/sandcorp_home/`)).json()).graph
  expect((await request.put(`${API_URL}/api/tenant/flows/${flow.id}/draft/`, { headers: auth, data: { graph } })).ok()).toBe(true)
  const saved = await (await request.post(`${API_URL}/api/tenant/flows/${flow.id}/versions/`, { headers: auth, data: { note: 'e2e 예제 ①' } })).json()
  expect((await request.post(`${API_URL}/api/tenant/flows/${flow.id}/deploy/`, { headers: auth, data: { version: saved.version.number } })).ok()).toBe(true)
  if (publicView) {
    expect((await request.patch(`${API_URL}/api/tenant/config/`, { headers: auth, data: { public_flow_view: true } })).ok()).toBe(true)
  }
  return { slug, flowName: flow.name }
}

test.describe('공개 흐름 보기 — 실제 스택', () => {
  test('익명으로 읽기 전용 보기 → 노드 설정 → 「고쳐 보기」가 체험 템플릿으로 간다', async ({ page, request }) => {
    const { slug, flowName } = await publicTenant(request, { publicView: true })

    // 데스크톱: 끌기·선 잇기·저장·시험 대화 없음
    await page.setViewportSize({ width: 1280, height: 800 })
    await page.goto(`${ADMIN_URL}/admin-ui/flows/${slug}`)
    await expect(page.getByRole('heading', { name: flowName })).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('읽기 전용 — 비밀·내부 주소·헤더·본문은 숨김')).toBeVisible()
    await expect(page.locator('.react-flow__node')).not.toHaveCount(0)
    await expect(page.locator('.react-flow__node.draggable')).toHaveCount(0)
    await expect(page.locator('.react-flow__handle.connectable')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /저장|배포|보내기/ })).toHaveCount(0)
    await expect(page.getByRole('tab', { name: '시험 대화' })).toHaveCount(0)
    expect(await page.evaluate(() => sessionStorage.getItem('agent_access'))).toBeNull() // 로그인 없이
    await page.locator('.react-flow__node', { hasText: 'N-02' }).first().click()
    const aside = page.getByRole('complementary', { name: '노드 설정' })
    await expect(aside.getByLabel('이름', { exact: true })).toHaveAttribute('readonly', '')
    await shot(page, 'public-desktop')
    expect(await overflow(page)).toEqual([])

    // 390폭 라이트·다크: 하단 「노드 설정」 시트(읽기 전용), 가로 넘침 없음
    await page.setViewportSize({ width: 390, height: 844 })
    await page.reload()
    await expect(page.getByRole('heading', { name: flowName })).toBeVisible({ timeout: 15000 })
    await expect(page.getByRole('navigation', { name: '하단 탭' }).getByRole('button')).toHaveText(['노드 설정'])
    expect(await overflow(page)).toEqual([])
    await shot(page, 'public-390-light')
    await page.locator('.react-flow__node', { hasText: 'N-02' }).first().click()
    const sheet = page.getByRole('dialog', { name: '노드 설정' })
    await expect(sheet.getByLabel('이름', { exact: true })).toHaveAttribute('readonly', '')
    expect(await overflow(page)).toEqual([])
    await shot(page, 'public-390-sheet')
    await page.evaluate(() => localStorage.setItem('admin_theme', 'dark'))
    await page.reload()
    await expect(page.locator('html')).toHaveClass(/dark/)
    await expect(page.getByRole('heading', { name: flowName })).toBeVisible({ timeout: 15000 })
    expect(await overflow(page)).toEqual([])
    await shot(page, 'public-390-dark')
    await page.evaluate(() => localStorage.setItem('admin_theme', 'light'))

    // 「고쳐 보기」 → /try?template=home(곧바로 홈 편집기, #195)
    await page.getByRole('link', { name: '고쳐 보기' }).click()
    await expect(page).toHaveURL(/\/admin-ui\/try\?template=home$/)
    await expect(page.locator('.react-flow__node').first()).toBeVisible({ timeout: 15000 })
    await expect(page.getByText('첫 저장 때 체험이 시작돼요')).toBeAttached() // 폰 폭에선 머리줄 안내를 숨긴다
  })

  test('공개 보기를 끈 조직은 없는 slug 와 같은 404 — 화면은 「공개된 흐름이 없습니다」', async ({ page, request }) => {
    const { slug } = await publicTenant(request, { publicView: false })
    const off = await request.get(`${API_URL}/api/public/flows/${slug}/`)
    const none = await request.get(`${API_URL}/api/public/flows/${slugOf()}/`)
    expect([off.status(), none.status()]).toEqual([404, 404])
    expect(await off.text()).toBe(await none.text())

    await page.goto(`${ADMIN_URL}/admin-ui/flows/${slug}`)
    await expect(page.getByText('공개된 흐름이 없습니다.')).toBeVisible({ timeout: 15000 })
    await expect(page.locator('.react-flow__node')).toHaveCount(0)
  })
})
