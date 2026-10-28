// 폰 모드(#38): 390폭에서 캔버스는 읽기 전용(팬·줌·노드 누르기만), 노드 설정은 하단 시트, 시험 대화는 하단 탭.
// SHOTS_DIR 이 있으면 라이트·다크 390폭 증거 사진과 데스크톱 한 장을 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { settled } = require('./settle')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const SHOTS = process.env.SHOTS_DIR
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${process.env.SHOT_TAG || 'after'}-${name}.png`) })
}

/** 가로 넘침 없음(문서 + 시트·하단 탭) — scrollWidth 로 잰다. */
const noHorizontalOverflow = (page) => page.evaluate(() => {
  const de = document.documentElement
  const bad = []
  if (de.scrollWidth > de.clientWidth) bad.push(`html ${de.scrollWidth}>${de.clientWidth}`)
  for (const sel of ['[data-testid="phone-sheet"]', 'nav[aria-label="하단 탭"]', '[data-testid="flow-canvas"]']) {
    const el = document.querySelector(sel)
    if (el && el.scrollWidth > el.clientWidth) bad.push(`${sel} ${el.scrollWidth}>${el.clientWidth}`)
  }
  return bad
})
/** 모든 스크롤 조상(캔버스부터 html 까지)과 window 가 맨 위에 있다 — 스크롤 끌림 없음. */
const noScrollDrag = (page) => page.evaluate(() => {
  const out = []
  for (let el = document.querySelector('[data-testid="flow-canvas"]'); el; el = el.parentElement) if (el.scrollTop !== 0) out.push(`${el.tagName}.${String(el.className).slice(0, 30)} ${el.scrollTop}`)
  if (window.scrollY !== 0) out.push(`window ${window.scrollY}`)
  return out
})
/** 폰 편집기는 한 화면에 들어간다 — 문서가 뷰포트보다 길면 아무 스크롤(Playwright 의 클릭 보정 포함)이 window 를 끌고 간다(#160). */
const noVerticalOverflow = (page) => page.evaluate(() => document.scrollingElement.scrollHeight - document.scrollingElement.clientHeight)
/** 화면 맞춤 버튼을 누르고 뷰포트가 멈출 때까지 기다린다 — 앞 단계의 끌기(팬)가 그래프를 화면 밖으로 밀었어도 노드를 누를 수 있게(#167). */
const refit = async (page) => {
  await page.getByRole('button', { name: 'fit view' }).click()
  let last = ''
  await expect.poll(async () => {
    const now = await viewportOf(page)
    const same = now === last
    last = now
    return same
  }, { intervals: [100, 100, 200, 300] }).toBe(true)
}
const viewportOf = (page) => page.locator('.react-flow__viewport').evaluate((el) => getComputedStyle(el).transform)

test.describe('폰 모드 — 실제 스택', () => {
  test('/try 고쳐 보기: 팬은 되고 선 잇기·노드 추가는 안 된다 → 노드 설정 시트 → 시험 대화 한 턴', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
    const nodes = page.locator('.react-flow__node')
    await expect(nodes.first()).toBeVisible()
    await expect(page.getByRole('navigation', { name: '하단 탭' })).toBeVisible()
    // 노드 추가 UI 없음
    await expect(page.getByText(/눌러서 더하거나/)).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'AI 답변', exact: true })).toHaveCount(0)
    expect(await page.locator('.react-flow__node.draggable').count()).toBe(0)
    expect(await page.locator('.react-flow__handle.connectable').count()).toBe(0)
    await shot(page, 'phone-view-light')

    // 팬은 된다
    const nodeCount = await nodes.count()
    const edgeCount = await page.locator('.react-flow__edge').count()
    const before = await viewportOf(page)
    const pane = await page.locator('.react-flow__pane').boundingBox()
    await page.mouse.move(pane.x + 20, pane.y + 40)
    await page.mouse.down()
    await page.mouse.move(pane.x + 90, pane.y + 90, { steps: 8 })
    await page.mouse.up()
    expect(await viewportOf(page)).not.toBe(before)

    // 선 잇기 시도: 손잡이에서 다른 노드로 끌어도 선이 늘지 않는다
    const a = await page.locator('.react-flow__handle.source').first().boundingBox()
    const b = await page.locator('.react-flow__handle.target').last().boundingBox()
    await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
    await page.mouse.down()
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 10 })
    await page.mouse.up()
    await settled(page)
    expect(await page.locator('.react-flow__edge').count()).toBe(edgeCount)
    expect(await nodes.count()).toBe(nodeCount)
    expect(await noScrollDrag(page)).toEqual([])

    // 노드를 누르면 설정 시트 — 읽기 전용 캔버스에서 손잡이를 끌면 팬이 될 수 있으니(트레이스에서 그래프가 화면 밖으로 밀린 것을 봄) 먼저 화면에 맞춘다
    await refit(page)
    await page.locator('[data-node-id="general"]').click()
    const sheet = page.getByRole('dialog', { name: '노드 설정' })
    await expect(sheet).toBeVisible()
    await expect(sheet.getByRole('button', { name: /노드 삭제/ })).toHaveCount(0)
    await sheet.getByRole('tab', { name: '고급' }).click()
    const cfg = sheet.locator('#node-config')
    const parsed = JSON.parse(await cfg.inputValue())
    parsed.instructions = '폰에서 고침'
    await cfg.fill(JSON.stringify(parsed))
    await sheet.getByRole('button', { name: '설정 적용' }).click()
    await expect(page.getByText('초안 저장됨')).toBeVisible()
    expect(await noHorizontalOverflow(page)).toEqual([])
    expect(await noVerticalOverflow(page)).toBeLessThanOrEqual(0) // 머리줄에 상태 표시가 붙어 줄이 접혀도 문서가 넘치지 않는다(#160)
    await shot(page, 'phone-sheet-light')

    // 하단 탭에서 시험 대화 한 턴
    await page.getByRole('navigation', { name: '하단 탭' }).getByRole('button', { name: '시험 대화' }).click()
    const chat = page.getByRole('dialog', { name: '시험 대화' })
    await chat.getByLabel('시험 메시지').fill('안녕하세요')
    await chat.getByRole('button', { name: '보내기' }).click()
    await expect(chat.getByRole('region', { name: '시험 대화' }).locator('li').first().locator('[data-reply], :text("사람 넘김 노드에 닿았습니다")').first()).toBeVisible({ timeout: 90_000 })
    expect(await noHorizontalOverflow(page)).toEqual([])
    expect(await noScrollDrag(page)).toEqual([])
    await shot(page, 'phone-test-light')

    if (SHOTS) {
      await page.evaluate(() => localStorage.setItem('admin_theme', 'dark'))
      await page.reload()
      await expect(nodes.first()).toBeVisible()
      await shot(page, 'phone-view-dark')
      await page.locator('[data-node-id="general"]').click()
      await expect(page.getByRole('dialog', { name: '노드 설정' })).toBeVisible()
      await shot(page, 'phone-sheet-dark')
      await page.getByRole('navigation', { name: '하단 탭' }).getByRole('button', { name: '시험 대화' }).click()
      await expect(page.getByLabel('시험 메시지')).toBeVisible()
      await shot(page, 'phone-test-dark')
      await page.evaluate(() => localStorage.setItem('admin_theme', 'light'))
      await page.setViewportSize({ width: 1600, height: 900 })
      await page.reload()
      await expect(nodes.first()).toBeVisible()
      await expect(page.getByRole('navigation', { name: '하단 탭' })).toHaveCount(0)
      expect(await page.locator('.react-flow__node.draggable').count()).toBeGreaterThan(0)
      await shot(page, 'desktop-editor')
    }
  })

  test('로그인 편집기도 같은 규칙: 폰 폭에서 팔레트가 없고 끌기·연결이 꺼진다 / PC 폭은 그대로', async ({ page }) => {
    const tenant = await provisionTenant(`Phone ${Date.now()}`)
    await openMemberSession(page, ADMIN_URL, tenant.token, 'tenant/flows')
    await page.getByLabel('새 흐름 이름').fill('폰 규칙')
    await page.getByRole('button', { name: '만들기' }).click()
    await page.waitForURL(/\/tenant\/flows\/[0-9a-f-]{36}$/)
    await page.getByRole('button', { name: '시작', exact: true }).click()
    await expect(page.locator('.react-flow__node')).toHaveCount(1)
    await expect(page.getByText('초안 저장됨')).toBeVisible()
    expect(await page.locator('.react-flow__node.draggable').count()).toBe(1)

    await page.setViewportSize({ width: 390, height: 844 })
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(1)
    await expect(page.getByRole('navigation', { name: '하단 탭' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'AI 답변', exact: true })).toHaveCount(0)
    expect(await page.locator('.react-flow__node.draggable').count()).toBe(0)
    expect(await noHorizontalOverflow(page)).toEqual([])
    expect(await noScrollDrag(page)).toEqual([])
    await shot(page, 'phone-tenant-editor')
  })
})
