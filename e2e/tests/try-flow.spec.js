// /try (#37, #195): 실제 스택에서 방문 즉시 편집기 → 노드 설정 바꾸기(첫 저장에 세션) → 새로고침 → 시험 대화 → 판단 기록.
// SHOTS_DIR 이 있으면 라이트·다크·390폭 증거 사진을 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')
const { settled } = require('./settle')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const SHOTS = process.env.SHOTS_DIR
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${process.env.SHOT_TAG || 'after'}-${name}.png`) })
}

test('/try 카드 고르기 → 「고쳐 보기」는 이동만(세션 없음), 예전 /try/view 주소는 같은 템플릿 편집기로', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 })
  const sessionNow = () => page.evaluate(async () => (await fetch('/api/try/session/')).json())

  // 카드 고르기(bare /try): 「흐름 보기」는 없고, 「고쳐 보기」는 이동만 한다(#199) — POST 없이 같은 템플릿 편집기
  const posts = []
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().includes('/api/try/sessions/')) posts.push(r.url()) })
  await page.goto(`${ADMIN_URL}/admin-ui/try`)
  const card = page.locator('[data-template="sandcorp_home"]')
  await expect(card.getByRole('link', { name: '고쳐 보기' })).toBeVisible()
  await expect(page.getByRole('link', { name: '흐름 보기' })).toHaveCount(0)
  await shot(page, 'try-picker-1600')
  await card.getByRole('link', { name: '고쳐 보기' }).click()
  await expect(page).toHaveURL(/\/try\?template=home$/)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  expect(await sessionNow()).toBeNull()
  expect(posts).toEqual([])
  // 예전 /try/view/:id 주소는 같은 템플릿 편집기로 replace
  await page.goto(`${ADMIN_URL}/admin-ui/try/view/sandcorp_home`)
  await expect(page).toHaveURL(/\/try\?template=home$/)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await expect(page.getByText(/읽기 전용/)).toHaveCount(0)

})

// 한 시험이 고르기·편집·시험 대화·만료를 다 걷지 않는다 — 3초 한도 안에서 단계별로 나눈다(#257).
test('/try?template=home → 곧바로 편집기(세션 없음) → 노드 설정(첫 저장 = 세션) → 새로고침 → 시험 대화 → 판단 기록', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 })
  const sessionNow = () => page.evaluate(async () => (await fetch('/api/try/session/')).json())

  // 방문 즉시 편집기(#195): 카드·버튼 없이 편집기, 세션은 아직 없다
  const statuses = []
  page.on('response', (r) => { if (r.url().includes('/api/try/')) statuses.push(r.status()) })
  await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await expect(page.getByRole('button', { name: /고쳐 보기/ })).toHaveCount(0)
  expect(await sessionNow()).toBeNull()
  await expect(page.getByText('첫 저장 때 체험이 시작돼요')).toBeVisible()
  await expect(page.getByText('버전 이력')).toHaveCount(0)
  expect(statuses.filter((s) => s >= 400)).toEqual([]) // 무세션 편집기는 401 try_no_session 을 받지 않는다
  await shot(page, 'try-lazy-1600')

  // 노드 설정 바꾸기(고급 JSON) — 새로고침해도 남는다
  const aside = page.locator('aside[aria-label="노드 설정"]')
  await page.locator('[data-node-id="general"]').click()
  await aside.getByRole('tab', { name: '고급' }).click()
  const cfg = aside.locator('#node-config')
  const parsed = JSON.parse(await cfg.inputValue())
  parsed.instructions = '항상 한 문장으로 답한다. (체험에서 고침)'
  await cfg.fill(JSON.stringify(parsed))
  await aside.getByRole('button', { name: '설정 적용' }).click()
  await expect(page.getByText('초안 저장됨')).toBeVisible()
  const made = await sessionNow() // 첫 저장이 세션을 만들었다
  expect(made?.template).toBe('sandcorp_home')
  await expect(page.getByText(/남은 대화 \d+턴/)).toBeVisible()
  await expect(page.getByRole('link', { name: '이 흐름으로 가입' })).toHaveAttribute('href', /then=try_claim/)
  await page.reload()
  await expect(page).toHaveURL(/\/try\?template=home$/)
  expect((await sessionNow())?.flow_id).toBe(made.flow_id) // 새로고침 뒤 같은 사본
  await page.locator('[data-node-id="general"]').click()
  await aside.getByRole('tab', { name: '고급' }).click()
  await expect(aside.locator('#node-config')).toHaveValue(/체험에서 고침/)

  await aside.getByRole('tab', { name: '시험 대화' }).click()
  await aside.getByLabel('시험 메시지').fill('안녕하세요')
  await aside.getByRole('button', { name: '보내기' }).click()
  const chat = aside.getByRole('region', { name: '시험 대화' })
  // Fake LLM 이 분류를 어떻게 하든 한 턴이 돌아 답 또는 넘김 안내가 나온다
  await expect(chat.locator('li').first().locator('[data-reply], :text("사람 넘김 노드에 닿았습니다")').first()).toBeVisible({ timeout: 90_000 })
  await aside.getByRole('tab', { name: '판단 기록' }).click()
  const log = aside.getByRole('region', { name: '판단 기록' })
  await expect(log.getByText(/^턴 1$/)).toBeVisible()
  await expect(log.getByText(/세션 합계/)).toBeVisible()
  await shot(page, 'try-log-1600')

  if (SHOTS) {
    for (const theme of ['light', 'dark']) {
      await page.evaluate((t) => localStorage.setItem('admin_theme', t), theme)
      await page.reload()
      await expect(page.locator('.react-flow__node').first()).toBeVisible()
      await shot(page, `try-editor-${theme}-1600`)
      await page.goto(`${ADMIN_URL}/admin-ui/try`)
      await expect(page.getByRole('link', { name: /이어서 고쳐 보기/ })).toBeVisible()
      await shot(page, `try-picker-${theme}-1600`)
      await page.goBack()
    }
    await page.setViewportSize({ width: 390, height: 844 })
    await page.evaluate(() => localStorage.setItem('admin_theme', 'light'))
    await page.reload()
    await expect(page.locator('.react-flow__node').first()).toBeVisible()
    await shot(page, 'try-editor-390')
    await page.goto(`${ADMIN_URL}/admin-ui/try`)
    await expect(page.getByRole('link', { name: /이어서 고쳐 보기/ })).toBeVisible()
    await shot(page, 'try-picker-390')
  }

})

// 쿠키가 사라지면(새 브라우저 문맥 = 체험 쿠키 없음) 옛 사본 주소는 만료 안내로 돌아온다
test('/try 쿠키가 없으면 사본 주소는 만료 안내(템플릿 고르기)로 돌아온다', async ({ page }) => {
  await page.goto(`${ADMIN_URL}/admin-ui/try/flow/00000000-0000-0000-0000-000000000000`)
  await expect(page).toHaveURL(/\/try\?expired=1/)
  await expect(page.getByText(/체험이 끝났거나 만료/)).toBeVisible()
})

test('첫 쓰기로 세션이 만들어지는 동안·직후 이어 친 이름이 화면과 새로고침 뒤에도 남는다(#196)', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 })
  const sessionNow = () => page.evaluate(async () => (await fetch('/api/try/session/')).json())
  await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await page.locator('[data-node-id="general"]').click()
  const name = page.locator('aside[aria-label="노드 설정"]').getByLabel('이름')
  await name.fill('')
  await name.pressSequentially('가') // 첫 글자
  await name.blur() // 첫 쓰기 — 세션 생성 시작
  await name.focus()
  await name.pressSequentially('나다라마', { delay: 120 }) // 생성 중·직후에도 이어 친다
  await name.blur()
  await expect(page.getByText('초안 저장됨')).toBeVisible()
  await expect(name).toHaveValue('가나다라마')
  await expect(page.locator('[data-node-id="general"]')).toContainText('가나다라마')
  expect(await sessionNow()).not.toBeNull()
  await page.reload()
  await expect(page.locator('[data-node-id="general"]')).toContainText('가나다라마') // 서버 사본에도 전체가 있다
})

test('세션 전에 옮긴 노드 위치가 새로고침 뒤에도 남고 세션은 만들어지지 않는다(#197)', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 })
  const sessionNow = () => page.evaluate(async () => (await fetch('/api/try/session/')).json())
  await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
  const node = page.locator('[data-node-id="general"]')
  await expect(node).toBeVisible()
  await settled(page) // 화면 맞춤 애니메이션이 끝난 뒤에 잰다
  const other = page.locator('[data-node-id="start"]')
  const rel = async () => { const a = await page.locator('[data-node-id="general"]').boundingBox(); const b = await other.boundingBox(); return { dx: (a.x - b.x) / a.width, dy: (a.y - b.y) / a.width } } // 다시 열면 화면 맞춤이 달라 이웃 노드와의 상대 위치로 잰다
  const relBefore = await rel()
  const dist = async () => { const r = await rel(); return Math.abs(r.dx - relBefore.dx) + Math.abs(r.dy - relBefore.dy) }
  for (let attempt = 0; attempt < 4 && (await dist()) < 0.08; attempt++) { // 끌기 시작이 간혹 씹혀 몇 번 다시 시도한다
    const box = await node.boundingBox()
    await page.mouse.move(box.x + 20, box.y + 12)
    await settled(page)
    await page.mouse.down()
    await page.mouse.move(box.x + 20 + 90, box.y + 12 + 70, { steps: 12 })
    await page.mouse.up()
    await settled(page)
  }
  expect(await dist()).toBeGreaterThan(0.08)
  await settled(page)
  const relMoved = await rel()
  expect(await sessionNow()).toBeNull() // 끌기만으로는 체험을 시작하지 않는다
  await page.reload()
  await expect(page.locator('[data-node-id="general"]')).toBeVisible()
  await settled(page)
  const relAfter = await rel()
  expect(Math.abs(relAfter.dx - relMoved.dx)).toBeLessThan(0.05)
  expect(Math.abs(relAfter.dy - relMoved.dy)).toBeLessThan(0.05)
  expect(await sessionNow()).toBeNull()
})

test('편집 중 세션이 만료돼도 첫 화면으로 튕기지 않고 안내 + 편집 유지, 다음 쓰기가 새 세션을 만든다(#197)', async ({ page, context }) => {
  await page.setViewportSize({ width: 1600, height: 900 })
  const sessionNow = () => page.evaluate(async () => (await fetch('/api/try/session/')).json())
  await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await page.locator('[data-node-id="general"]').click()
  const name = page.locator('aside[aria-label="노드 설정"]').getByLabel('이름')
  await name.fill('첫이름')
  await name.blur()
  await expect(page.getByText('초안 저장됨')).toBeVisible()
  expect(await sessionNow()).not.toBeNull()

  await context.clearCookies() // 24시간 만료 흉내: 세션 쿠키가 사라졌다
  await name.fill('둘째이름')
  await name.blur()
  await expect(page.getByTestId('try-notice')).toContainText('체험 시간이 지나')
  await expect(page).toHaveURL(/\/try\?template=home$/) // 첫 화면(?expired=1)으로 튕기지 않는다
  await expect(page.locator('[data-node-id="general"]')).toContainText('둘째이름')

  await name.fill('셋째이름') // 다음 쓰기: 새 세션
  await name.blur()
  await expect(page.getByText('초안 저장됨')).toBeVisible()
  await expect(page.getByTestId('try-notice')).toHaveCount(0)
  expect(await sessionNow()).not.toBeNull()
  await page.reload()
  await expect(page.locator('[data-node-id="general"]')).toContainText('셋째이름')
})
