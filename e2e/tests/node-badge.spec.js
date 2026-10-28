// 노드 배지(#241) — 시험 한 턴 뒤 시간·토큰·summary 배지, 오류 노드엔 배지 없음, 폰 폭에선 감추고 넘침 0. 상태 표시는 flow-live-canvas.spec.js(#240).
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
  await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
  await page.setViewportSize({ width, height: width < 600 ? 844 : 900 })
  await page.reload()
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


const badge = (page, id) => live(page, id).locator('[data-testid="node-badge"]')

test.describe('노드 배지', () => {
  test('시험 한 턴 뒤 노드마다 시간과 핵심 결과가 뜨고 노드 조작을 막지 않으며 새 대화에서 사라진다', async ({ page }) => {
    test.setTimeout(120_000)
    await setup(page, 1600)
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(badge(page, 'answer')).toBeVisible({ timeout: 30_000 })
    await expect(badge(page, 'answer')).toContainText(/\d+(ms|\.\d초|초)/)
    await expect(badge(page, 'answer')).toContainText(/답 \d+자/)
    await expect(badge(page, 'start')).toBeVisible()
    expect(await badge(page, 'answer').evaluate((el) => getComputedStyle(el).pointerEvents)).toBe('none')
    await shot(page, 'badge-done')

    // 새로고침해도 마지막 턴으로 다시 그린다(summary 는 저장 기록에 없어 시간·토큰·비용만)
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(2)
    await expect(badge(page, 'answer')).toBeVisible({ timeout: 30_000 })
    await expect(badge(page, 'answer')).not.toContainText(/답 \d+자/)

    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByRole('button', { name: '새 대화' }).click()
    await expect(badge(page, 'answer')).toHaveCount(0)
  })

  test('실패한 노드에는 배지가 없고 끝난 노드에는 있다', async ({ page }) => {
    test.setTimeout(120_000)
    await setup(page, 1600)
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요 [[fail]]')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'error', { timeout: 30_000 })
    await expect(badge(page, 'start')).toBeVisible()
    await expect(badge(page, 'answer')).toHaveCount(0)
  })

  test('다크 모드에서도 배지가 읽힌다', async ({ page }) => {
    test.setTimeout(120_000)
    await page.addInitScript(() => localStorage.setItem('admin_theme', 'dark'))
    await setup(page, 1600)
    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(badge(page, 'answer')).toBeVisible({ timeout: 30_000 })
    await shot(page, 'badge-dark')
  })

  test('폰 폭: 배지는 감추고 가로로 넘치지 않는다', async ({ page }) => {
    test.setTimeout(120_000)
    await setup(page, 390)
    await page.getByRole('navigation', { name: '하단 탭' }).getByRole('button', { name: '시험 대화' }).click()
    const chat = page.getByRole('dialog', { name: '시험 대화' })
    await chat.getByLabel('시험 메시지').fill('안녕하세요')
    await chat.getByRole('button', { name: '보내기' }).click()
    await expect(live(page, 'answer')).toHaveAttribute('data-live', 'done', { timeout: 30_000 })
    await expect(badge(page, 'answer')).toBeHidden()
    expect(await noOverflow(page)).toEqual([])
  })
})
