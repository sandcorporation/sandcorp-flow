// 버전 이력·배포·되돌리기·배포 요청/승인(#31): 실제 스택(Fake LLM)에서 편집기 화면으로 버전을 저장·배포하고,
// 위젯 대화가 배포된 버전을 따르는지 본다. 가드레일(들어오는 말 차단)의 고정 문구로 버전을 구분한다 — Fake LLM 의 답은 늘 같다.
// SHOTS_DIR 이 있으면 라이트·다크·1600×900, 390폭 증거 사진도 남긴다.
const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { settled } = require('./settle')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR
const TAG = process.env.SHOT_TAG || 'after'

async function shot(page, name, { both = false } = {}) {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  if (!both) return page.screenshot({ path: path.join(SHOTS, `${TAG}-${name}.png`) })
  for (const theme of ['light', 'dark']) {
    await page.evaluate((t) => document.documentElement.classList.toggle('dark', t === 'dark'), theme)
    await settled(page)
    await page.screenshot({ path: path.join(SHOTS, `${TAG}-${name}-${theme}-1600x900.png`) })
  }
  await page.evaluate(() => document.documentElement.classList.remove('dark'))
}

const graphSaying = (message) => ({
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 60 }, config: {} },
    { id: 'gate', kind: 'guardrail', code: 'N-02', name: '입력 검사', position: { x: 260, y: 60 },
      config: { rules: ['banned_words'], banned_words: ['안녕'], action: 'block', target: 'input', block_message: message } },
  ],
  edges: [{ from: 'start', to: 'gate' }],
})

async function visitorSays(browser, slug, visitor) {
  const ctx = await browser.newContext()
  const page = await ctx.newPage()
  await page.goto(`${WIDGET_URL}/embed/chatbot/${slug}/?visitor_id=${visitor}`)
  const box = page.locator('textarea[placeholder="메시지를 입력하세요..."]')
  await expect(box).toBeEnabled({ timeout: 15000 })
  await box.fill('안녕하세요')
  await page.click('button:has-text("전송")')
  const reply = page.locator('[data-role="assistant"]').last()
  await expect(reply).toBeVisible({ timeout: 30000 })
  const text = await reply.innerText()
  await ctx.close()
  return text
}

// 한 시험이 시나리오 전체를 걷지 않는다 — 3초 한도 안에서 한 가지씩 본다(#257). 앞 단계는 화면이 아니라 API 로 만든다.
async function prepare(name) {
  const tenant = await provisionTenant(`${name} ${Date.now()}`, 15000, { defaultFlow: true })
  expect(tenant.secondToken).toBeTruthy()
  const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
  const slug = `fv-${Date.now()}`
  expect((await api.patch('/api/tenant/slug/', { data: { slug } })).ok()).toBeTruthy()
  const flows = await (await api.get('/api/tenant/flows/')).json()
  const flow = flows.find((f) => f.is_public)
  const setDraft = async (message) => expect((await api.put(`/api/tenant/flows/${flow.id}/draft/`, { data: { graph: graphSaying(message) } })).ok()).toBeTruthy()
  const saveVersion = async (note) => {
    const res = await api.post(`/api/tenant/flows/${flow.id}/versions/`, { data: { note } })
    expect(res.ok()).toBeTruthy()
    return (await res.json()).version.number
  }
  const deploy = async (version) => expect((await api.post(`/api/tenant/flows/${flow.id}/deploy/`, { data: { version } })).ok()).toBeTruthy()
  return { tenant, api, slug, flow, setDraft, saveVersion, deploy }
}

async function openFlow(page, { tenant, flow }, token = tenant.token) {
  await openMemberSession(page, ADMIN_URL, token, `tenant/flows/${flow.id}`)
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  const versions = page.getByRole('region', { name: '버전 이력' })
  await expect(versions).toBeVisible()
  return versions
}

test.describe('흐름 버전·배포 — 실제 스택', () => {
  test.beforeEach(async ({ page }) => {
    page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text().slice(0, 300)) })
    await page.setViewportSize({ width: 1600, height: 900 })
  })

  test('버전 저장 → 배포(한 번 확인) → 위젯이 새 버전', async ({ page, browser }) => {
    const ctx = await prepare('Flow Versions Save')
    await ctx.setDraft('답변-ONE')
    const versions = await openFlow(page, ctx)
    await expect(versions.locator('li[data-version="1"]')).toContainText('배포 중') // 기본 흐름 v1
    await expect(versions.locator('li[data-version="1"]')).toContainText('시스템(기본 흐름)') // 만든 이 없는 시드 버전

    // 버전 저장(note) → 맨 위에 v2
    await versions.getByLabel('버전 메모').fill('첫 배포본')
    await versions.getByRole('button', { name: '버전 저장' }).click()
    await expect(versions.locator('li[data-version="2"]')).toContainText('첫 배포본')
    await expect(versions.locator('li').first()).toHaveAttribute('data-version', '2') // 최신이 맨 위

    // 시험 대화 없이 배포 → 한 번 확인
    await versions.getByRole('button', { name: 'v2 배포' }).click()
    await expect(versions.getByRole('group', { name: '배포 확인' })).toBeVisible()
    await shot(page, 'deploy-confirm', { both: true })
    await versions.getByRole('button', { name: '그래도 배포' }).click()
    await expect(versions.locator('li[data-version="2"]')).toContainText('배포 중')
    expect(await visitorSays(browser, ctx.slug, 'v-one')).toContain('답변-ONE')
  })

  test('두 번째 버전을 배포하면 이전 버전의 배포 중이 사라지고 위젯이 따른다', async ({ page, browser }) => {
    const ctx = await prepare('Flow Versions Second')
    await ctx.setDraft('답변-ONE')
    await ctx.deploy(await ctx.saveVersion('첫 배포본'))
    await ctx.setDraft('답변-TWO')
    const versions = await openFlow(page, ctx)
    await versions.getByLabel('버전 메모').fill('문구를 바꿈')
    await versions.getByRole('button', { name: '버전 저장' }).click()
    await expect(versions.locator('li[data-version="3"]')).toContainText('문구를 바꿈')
    await versions.getByRole('button', { name: 'v3 배포' }).click()
    await versions.getByRole('button', { name: '그래도 배포' }).click()
    await expect(versions.locator('li[data-version="3"]')).toContainText('배포 중')
    await expect(versions.locator('li[data-version="2"]')).not.toContainText('배포 중')
    await shot(page, 'history', { both: true })
    expect(await visitorSays(browser, ctx.slug, 'v-two')).toContain('답변-TWO')
  })

  test('버전 보기(읽기 전용) → 초안으로 → 되돌리기 한 번 → 새 세션은 옛 문구', async ({ page, browser }) => {
    const ctx = await prepare('Flow Versions Back')
    await ctx.setDraft('답변-ONE')
    await ctx.deploy(await ctx.saveVersion('첫 배포본'))
    await ctx.setDraft('답변-TWO')
    await ctx.deploy(await ctx.saveVersion('문구를 바꿈'))
    const versions = await openFlow(page, ctx)

    await versions.getByRole('button', { name: 'v2 보기' }).click()
    await expect(page.getByText('v2 보는 중')).toBeVisible()
    await expect(page.locator('.react-flow__node')).toHaveCount(2)
    await shot(page, 'view', { both: true })
    await page.getByRole('button', { name: '초안으로 돌아가기' }).click()
    await expect(page.getByText('v2 보는 중')).toHaveCount(0)

    await versions.getByRole('button', { name: '되돌리기' }).click()
    await expect(versions.locator('li[data-version="2"]')).toContainText('배포 중')
    expect(await visitorSays(browser, ctx.slug, 'v-back')).toContain('답변-ONE')

    if (SHOTS) {
      await page.setViewportSize({ width: 768, height: 844 })
      await versions.scrollIntoViewIfNeeded()
      await settled(page)
      await shot(page, 'history-768')
      const over = await page.evaluate(() => {
        const bad = []
        if (document.documentElement.scrollWidth > document.documentElement.clientWidth) bad.push('page')
        document.querySelectorAll('aside, section, ul, li, button').forEach((el) => { if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible') bad.push(el.tagName + ':' + (el.getAttribute('aria-label') || el.textContent.slice(0, 20))) })
        return bad
      })
      console.log('[overflow-768]', JSON.stringify(over))
      expect(over).toEqual([])
    }
  })

  test('승인을 켜면 배포는 요청이 되고 요청자 본인은 승인할 수 없다', async ({ page, browser }) => {
    const ctx = await prepare('Flow Versions Request')
    await ctx.setDraft('답변-ONE')
    await ctx.deploy(await ctx.saveVersion('첫 배포본'))
    expect((await ctx.api.patch('/api/tenant/config/', { data: { deploy_requires_approval: true } })).ok()).toBeTruthy()
    await ctx.setDraft('답변-THREE')
    const versions = await openFlow(page, ctx)
    await versions.getByLabel('버전 메모').fill('승인 받을 버전')
    await versions.getByRole('button', { name: '버전 저장' }).click()
    await expect(versions.locator('li[data-version="3"]')).toBeVisible()
    await versions.getByRole('button', { name: 'v3 배포 요청' }).click()
    await versions.getByRole('button', { name: '그래도 배포 요청' }).click()
    await expect(versions.getByText('v3 승인 대기')).toBeVisible()
    await expect(versions.getByRole('button', { name: '승인' })).toBeDisabled()
    await expect(versions.getByText(/다른 관리자가 승인/)).toBeVisible()
    await shot(page, 'request', { both: true })
    expect(await visitorSays(browser, ctx.slug, 'v-pending')).toContain('답변-ONE') // 아직 배포 전
  })

  test('다른 Admin 이 승인하면 배포되고 요청자 화면도 배포 중이 된다', async ({ page, browser }) => {
    const ctx = await prepare('Flow Versions Approve')
    await ctx.setDraft('답변-ONE')
    await ctx.deploy(await ctx.saveVersion('첫 배포본'))
    expect((await ctx.api.patch('/api/tenant/config/', { data: { deploy_requires_approval: true } })).ok()).toBeTruthy()
    await ctx.setDraft('답변-THREE')
    const requested = await ctx.api.post(`/api/tenant/flows/${ctx.flow.id}/deploy/`, { data: { version: await ctx.saveVersion('승인 받을 버전') } })
    expect(requested.status()).toBe(202)

    await openMemberSession(page, ADMIN_URL, ctx.tenant.secondToken, 'tenant/flows')
    const requests = page.getByRole('region', { name: '배포 요청' })
    await expect(requests).toContainText('v3 승인 대기')
    await shot(page, 'approve-list', { both: true })
    await requests.getByRole('button', { name: '승인' }).click()
    await expect(page.getByRole('region', { name: '배포 요청' })).toHaveCount(0)
    await expect(page.getByText('v3 배포 중')).toBeVisible()
    expect(await visitorSays(browser, ctx.slug, 'v-approved')).toContain('답변-THREE')

    // 요청자 화면도 열면 v3 배포 중
    const versions = await openFlow(page, ctx)
    await expect(versions.locator('li[data-version="3"]')).toContainText('배포 중')
  })

  test('노드 카드: 제목과 갈래 이름이 겹치지 않는다(#98)', async ({ page }) => {
    const tenant = await provisionTenant(`Flow Overlap ${Date.now()}`)
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    const graph = {
      nodes: [
        { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
        { id: 'ask', kind: 'ai_answer', code: 'N-04', name: '요구사항이 다 모였나', position: { x: 260, y: 40 }, config: { instructions: '모았는지 본다', await_reply: true } },
        { id: 'done', kind: 'ai_answer', code: 'N-05', name: '정리', position: { x: 560, y: 0 }, config: { instructions: '정리한다' } },
        { id: 'side', kind: 'handoff', code: 'N-06', name: '사람 넘김', position: { x: 560, y: 200 }, config: { reason: '필요' } },
      ],
      edges: [{ from: 'start', to: 'ask' }, { from: 'ask', to: 'done' }, { from: 'ask', to: 'side', handle: 'handoff' }],
    }
    const made = await (await api.post('/api/tenant/flows/', { data: { name: '겹침 확인', graph } })).json()
    await page.setViewportSize({ width: 1600, height: 900 })
    await openMemberSession(page, ADMIN_URL, tenant.token, `tenant/flows/${made.id}`)
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
    await settled(page)
    await shot(page, 'node-overlap')
    const card = page.locator('[data-node-id="ask"]')
    await expect(card.locator('[data-exit]').first()).toBeVisible()
    const title = await card.locator('h5').boundingBox()
    const exits = await card.locator('[data-exit]').evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height } }))
    expect(exits.length).toBeGreaterThan(1)
    for (const r of exits) expect(r.y).toBeGreaterThanOrEqual(title.y + title.height - 1) // 갈래 이름은 제목 줄 아래
  })
})
