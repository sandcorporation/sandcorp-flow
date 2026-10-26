// /try (#37, #195): 실제 스택에서 방문 즉시 편집기 → 노드 설정 바꾸기(첫 저장에 세션) → 새로고침 → 시험 대화 → 판단 기록.
// SHOTS_DIR 이 있으면 라이트·다크·390폭 증거 사진을 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect } = require('@playwright/test')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const SHOTS = process.env.SHOTS_DIR
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${process.env.SHOT_TAG || 'after'}-${name}.png`) })
}

test('/try?template=home → 곧바로 편집기(세션 없음) → 노드 설정(첫 저장 = 세션) → 새로고침 → 시험 대화 → 판단 기록', async ({ page }) => {
  test.setTimeout(180_000)
  await page.setViewportSize({ width: 1600, height: 900 })
  const sessionNow = () => page.evaluate(async () => (await fetch('/api/try/session/')).json())

  // 카드 고르기(bare /try)의 흐름 보기는 세션 없이 읽기 전용
  await page.goto(`${ADMIN_URL}/admin-ui/try`)
  const card = page.locator('[data-template="sandcorp_home"]')
  await shot(page, 'try-picker-1600')
  await card.getByRole('link', { name: '흐름 보기' }).click()
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await expect(page.getByText(/읽기 전용/).first()).toBeVisible()
  await expect(page.getByRole('tab', { name: '시험 대화' })).toHaveCount(0)
  await shot(page, 'try-view-1600')

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

  // 쿠키가 사라지면 템플릿 고르기로 돌아온다
  await page.goBack().catch(() => {})
  await page.goto(page.url())
  await page.context().clearCookies()
  await page.goto(`${ADMIN_URL}/admin-ui/try`)
  await page.goto(`${ADMIN_URL}/admin-ui/try/flow/00000000-0000-0000-0000-000000000000`)
  await expect(page).toHaveURL(/\/try\?expired=1/)
  await expect(page.getByText(/체험이 끝났거나 만료/)).toBeVisible()
})
