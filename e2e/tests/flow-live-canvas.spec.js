// 캔버스 실시간 노드 상태(#240) — 실제 스택(fake-llm 지연·실패 마커)에서 실행 중 → 끝남, 오류 빨강, 턴 끝 호박색 경로, 폰 폭.
// fake-llm 마커: [[delay:N]] N초 지연, [[fail]] 400 오류. SHOTS_DIR 이 있으면 증거 사진을 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${process.env.SHOT_TAG || 'after'}-${name}.png`) })
}

const GRAPH = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
    { id: 'answer', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 40 }, config: { instructions: '짧게 답한다.' } },
  ],
  edges: [{ from: 'start', to: 'answer' }],
}

async function setup(page, width) {
  const tenant = await provisionTenant(`Live Canvas ${Date.now()}`)
  const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
  const made = await (await api.post('/api/tenant/flows/', { data: { name: '실시간 흐름', graph: GRAPH } })).json()
  await page.setViewportSize({ width, height: width < 600 ? 844 : 900 }) // 폭을 먼저 정하고 한 번만 연다(새로고침 없이, #257)
  await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
}
const live = (page, id) => page.locator(`[data-node-id="${id}"]`)
const noOverflow = (page) => page.evaluate(() => {
  const de = document.documentElement
  const bad = []
  if (de.scrollWidth > de.clientWidth) bad.push(`html ${de.scrollWidth}>${de.clientWidth}`)
  for (const sel of ['[data-testid="phone-sheet"]', 'nav[aria-label="하단 탭"]', '[data-testid="flow-canvas"]']) {
    const el = document.querySelector(sel)
    if (el && el.scrollWidth > el.clientWidth) bad.push(`${sel} ${el.scrollWidth}>${el.clientWidth}`)
  }
  return bad
})

test.describe('캔버스 실시간 노드 상태', () => {
  test('느린 답변 노드는 실행 중으로 보였다가 끝남이 되고, 턴이 끝나면 호박색 경로로 이어진다', async ({ page }) => {
    await setup(page, 1600)
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요 [[delay:0.5]]')
    await aside.getByRole('button', { name: '보내기' }).click()

    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'running', { timeout: 20_000 })
    await expect(live(page, 'start')).toHaveAttribute('data-live', 'done')
    await expect(page.locator('.react-flow__edge.animated')).toHaveCount(1) // 시작 → 답변: 지나간 선이 흐른다
    await shot(page, 'live-running')
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'done', { timeout: 30_000 })
    await expect(aside.locator('[data-reply]')).toBeVisible()
    await expect(live(page, 'answer')).toHaveAttribute('data-active', 'true') // 호박색 경로
    await expect(page.locator('.react-flow__edge.animated')).toHaveCount(0) // 턴이 끝나면 흐름 애니메이션은 멈춘다
    await shot(page, 'live-done')
  })

  test('새 대화는 상태를 비운다', async ({ page }) => {
    await setup(page, 1600)
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'done', { timeout: 30_000 })
    await expect(live(page, 'answer')).toHaveAttribute('data-active', 'true')
    await aside.getByRole('button', { name: '새 대화' }).click()
    await expect(live(page, 'answer')).not.toHaveAttribute('data-live', /.+/)
    await expect(live(page, 'answer')).not.toHaveAttribute('data-active', 'true')
  })

  test('실패한 노드는 빨갛게 남고 새 턴이 시작되면 비워진다', async ({ page }) => {
    await setup(page, 1600)
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요 [[fail]]')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'error', { timeout: 30_000 })
    await expect(live(page, 'start')).toHaveAttribute('data-live', 'done')
    await expect(live(page, 'answer').locator('[data-live-mark="error"]')).toHaveAttribute('aria-label', 'N-02 오류')
    await shot(page, 'live-error')

    await aside.getByLabel('시험 메시지').fill('다시 [[delay:0.5]]')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(live(page, 'answer')).not.toHaveAttribute('data-live', 'error', { timeout: 20_000 })
  })

  test('다크 모드에서도 상태 표시가 보인다', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('admin_theme', 'dark'))
    await setup(page, 1600)
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요 [[delay:0.5]]')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'running', { timeout: 20_000 })
    await shot(page, 'live-running-dark')
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'done', { timeout: 30_000 })
    await shot(page, 'live-done-dark')
  })

  test('움직임 줄이기: 애니메이션 없이 색만 바뀐다', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await setup(page, 1600)
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요 [[delay:0.5]]')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'running', { timeout: 20_000 })
    expect(await live(page, 'answer').evaluate((el) => getComputedStyle(el).animationName)).toBe('none')
    expect(await page.locator('.react-flow__edge.animated path').first().evaluate((el) => getComputedStyle(el).animationName)).toBe('none')
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'done', { timeout: 30_000 })
  })

  test('폰 폭: 하단 시트에서 돌려도 캔버스에 상태가 보이고 가로로 넘치지 않는다', async ({ page }) => {
    await setup(page, 390)
    await page.getByRole('navigation', { name: '하단 탭' }).getByRole('button', { name: '시험 대화' }).click()
    const chat = page.getByRole('dialog', { name: '시험 대화' })
    await chat.getByLabel('시험 메시지').fill('안녕하세요 [[delay:0.5]]')
    await chat.getByRole('button', { name: '보내기' }).click()
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'running', { timeout: 20_000 })
    expect(await noOverflow(page)).toEqual([])
    await shot(page, 'live-phone-running')
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'done', { timeout: 30_000 })
    expect(await noOverflow(page)).toEqual([])
    await shot(page, 'live-phone-done')
  })
})
