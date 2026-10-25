// 시험 대화·판단 기록(#30): 실제 스택(Fake LLM)에서 초안으로 두 턴을 돌려 판단 기록과 캔버스 강조를 본다.
// SHOTS_DIR 이 있으면 라이트·다크·1600×900, 390폭 증거 사진도 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const TAG = process.env.SHOT_TAG || 'after'
const shot = async (page, name) => {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${TAG}-${name}.png`) })
}

const GRAPH = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
    { id: 'ask', kind: 'ai_answer', code: 'N-02', name: '되묻기', position: { x: 260, y: 40 }, config: { instructions: '용건을 한 줄로 되묻는다.', await_reply: true } },
    { id: 'draft', kind: 'ai_answer', code: 'N-03', name: '초안 답변', position: { x: 520, y: 40 }, config: { instructions: '짧게 답한다.' } },
    { id: 'side', kind: 'handoff', code: 'N-04', name: '사람 넘김', position: { x: 520, y: 220 }, config: { reason: '사람이 필요함' } },
  ],
  edges: [{ from: 'start', to: 'ask' }, { from: 'ask', to: 'draft' }, { from: 'ask', to: 'side', handle: 'handoff' }],
}

test.describe('흐름 편집기 — 시험 대화·판단 기록(실제 스택)', () => {
  test('두 턴을 돌리면 판단 기록에 노드별 항목·토큰·비용이 쌓이고, 항목을 누르면 캔버스 노드가 강조된다', async ({ page }) => {
    test.setTimeout(150_000)
    const tenant = await provisionTenant(`Flow Test Chat ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '시험 흐름', graph: GRAPH } })).json()
    await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
    await page.setViewportSize({ width: 1600, height: 900 })
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(4)

    const aside = page.locator('aside[aria-label="노드 설정"]')
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await aside.getByLabel('시험 메시지').fill('안녕하세요, 견적이 궁금해요')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(aside.getByRole('region', { name: '시험 대화' }).locator('li').first().locator('[data-reply]')).toBeVisible({ timeout: 60_000 })
    await expect(aside.getByRole('button', { name: '보내기' })).toBeDisabled() // 입력이 비어 다시 막힌다
    await aside.getByLabel('시험 메시지').fill('웹사이트 리뉴얼이에요')
    await aside.getByRole('button', { name: '보내기' }).click()
    await expect(aside.getByRole('region', { name: '시험 대화' }).locator('li')).toHaveCount(2)
    await expect(aside.getByRole('region', { name: '시험 대화' }).locator('li').nth(1).locator('[data-reply]')).toBeVisible({ timeout: 60_000 })
    await shot(page, 'chat-1600x900')

    await aside.getByRole('tab', { name: '판단 기록' }).click()
    const log = aside.getByRole('region', { name: '판단 기록' })
    await expect(log.getByText(/^턴 \d$/)).toHaveCount(2)
    await expect(log.getByText('N-02 되묻기')).toBeVisible()
    await expect(log.getByText('N-03 초안 답변')).toBeVisible()
    await expect(log.getByText('1/1').first()).toBeVisible() // Fake LLM 이 보고한 토큰
    await expect(log.getByText('$0.0000').first()).toBeVisible() // Fake LLM 모델명이 단가표에 있어 비용이 계산된다(#85)
    await expect(log.getByText('단가 미상')).toHaveCount(0)
    await expect(log.getByText(/세션 합계/)).toBeVisible()
    expect(await log.innerText()).not.toContain('__flow_finish__')

    // 첫 턴의 되묻기 항목 → 그 노드 강조(호박색) + 지나간 경로, 안 지난 노드는 꺼짐
    await log.getByRole('button', { name: /N-02 되묻기/ }).first().click()
    await expect(page.locator('[data-node-id="ask"]')).toHaveAttribute('data-active', 'true')
    await expect(page.locator('[data-node-id="start"]')).toHaveAttribute('data-active', 'true')
    await expect(page.locator('[data-node-id="side"]')).not.toHaveAttribute('data-active', 'true')
    await shot(page, 'log-1600x900')

    // 새로고침해도 마지막 시험 세션의 판단 기록이 되돌아온다(저장된 판단 기록에서 복원, #85)
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
    await aside.getByRole('tab', { name: '판단 기록' }).click()
    await expect(aside.getByRole('region', { name: '판단 기록' }).getByText(/^턴 \d$/)).toHaveCount(2)
    await expect(aside.getByRole('region', { name: '판단 기록' }).getByText(/세션 합계/)).toBeVisible()
    await shot(page, 'log-restored-1600x900')

    // 설정 탭으로 가면 강조가 꺼지고, 새 대화는 시험 대화를 비운다
    await aside.getByRole('tab', { name: '시험 대화' }).click()
    await expect(page.locator('[data-node-id="ask"]')).not.toHaveAttribute('data-active', 'true')
    await aside.getByRole('button', { name: '새 대화' }).click()
    await expect(aside.getByRole('region', { name: '시험 대화' }).locator('li')).toHaveCount(0)

    if (SHOTS) {
      // 같은 장면을 다시 만들어 라이트·다크·390폭을 남긴다
      for (const theme of ['light', 'dark']) {
        await page.evaluate((t) => localStorage.setItem('admin_theme', t), theme)
        await page.setViewportSize({ width: 1600, height: 900 })
        await page.reload()
        await expect(page.locator('.react-flow__node')).toHaveCount(4)
        await aside.getByRole('tab', { name: '시험 대화' }).click()
        for (const msg of ['안녕하세요, 견적이 궁금해요', '웹사이트 리뉴얼이에요']) {
          const n = await aside.getByRole('region', { name: '시험 대화' }).locator('li').count()
          await aside.getByLabel('시험 메시지').fill(msg)
          await aside.getByRole('button', { name: '보내기' }).click()
          await expect(aside.getByRole('region', { name: '시험 대화' }).locator('li').nth(n).locator('[data-reply]')).toBeVisible({ timeout: 60_000 })
        }
        await shot(page, `chat-${theme}-1600x900`)
        await aside.getByRole('tab', { name: '판단 기록' }).click()
        await aside.getByRole('region', { name: '판단 기록' }).getByRole('button', { name: /N-03 초안 답변/ }).first().click()
        await page.waitForTimeout(400)
        await shot(page, `log-${theme}-1600x900`)
        if (theme === 'light') {
          await page.setViewportSize({ width: 768, height: 844 })
          await aside.scrollIntoViewIfNeeded()
          await page.waitForTimeout(400)
          await shot(page, 'log-768')
          const over = await page.evaluate(() => {
            const bad = []
            if (document.documentElement.scrollWidth > document.documentElement.clientWidth) bad.push('page')
            document.querySelectorAll('aside, section, ul, ol, li, button').forEach((el) => { if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible' && el.closest('aside')) bad.push(el.tagName + ':' + (el.getAttribute('aria-label') || el.textContent.slice(0, 20))) })
            return bad
          })
          console.log('[overflow-768]', JSON.stringify(over))
          expect(over).toEqual([])
        }
      }
    }
  })
})
