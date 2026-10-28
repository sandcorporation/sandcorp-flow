// 방문 즉시 편집기의 세션 지연 생성(#194~#199): 진입 직후 POST 0 → 첫 저장 POST 1 → 둘째 저장에도 POST 1,
// IP 한도로 거절돼도 편집은 화면에 남고 새로고침 뒤에도 남는다.
// 한도(429 try_ip_limit)는 실제로 소진하면 같은 IP 를 쓰는 다른 시험과 간섭하므로, 세션 생성 응답만 서버가 준 모양 그대로 대역으로 낸다.
const { test, expect } = require('@playwright/test')
const { trackRequests } = require('./settle')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const isPost = (r) => r.method() === 'POST' && new URL(r.url()).pathname === '/api/try/sessions/'

async function rename(page, name) {
  const aside = page.locator('aside[aria-label="노드 설정"]')
  await page.locator('[data-node-id="general"]').click()
  const field = aside.getByLabel('이름')
  await field.fill(name)
  await field.blur()
}

test('진입 직후 세션 POST 0 → 첫 저장 POST 1 → 둘째 저장에도 POST 1', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1400, height: 900 })
  const posts = []
  page.on('request', (r) => { if (isPost(r)) posts.push(r.postData()) })
  const net = trackRequests(page)
  await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await net.idle()
  expect(posts).toHaveLength(0)

  await rename(page, '첫 저장 이름')
  await expect(page.getByText('초안 저장됨')).toBeVisible()
  await expect.poll(() => posts.length).toBe(1)
  const first = await page.evaluate(async () => (await fetch('/api/try/session/')).json())
  expect(first?.template).toBe('sandcorp_home')

  const patched = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes(`/api/try/flows/${first.flow_id}/`))
  await rename(page, '둘째 저장 이름')
  expect((await patched).status()).toBeLessThan(300)
  await net.idle()
  expect(posts).toHaveLength(1) // 세션은 한 번만 만든다
  const second = await page.evaluate(async () => (await fetch('/api/try/session/')).json())
  expect(second.flow_id).toBe(first.flow_id)
})

test('IP 한도로 세션 생성이 거절돼도 고친 내용은 화면에 남고 새로고침 뒤에도 남는다', async ({ page }) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.route(/\/api\/try\/sessions\/$/, (route) => route.request().method() === 'POST'
    ? route.fulfill({ status: 429, contentType: 'application/json', body: JSON.stringify({ detail: '오늘 이 네트워크에서 만들 수 있는 체험을 모두 만들었어요. 내일 다시 찾아 주세요.', code: 'try_ip_limit' }) })
    : route.continue())
  await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await rename(page, '한도에 막힌 이름')
  await expect(page.getByRole('alert').filter({ hasText: '내일 다시 찾아 주세요' }).first()).toBeVisible()
  await expect(page.locator('aside[aria-label="노드 설정"]').getByLabel('이름')).toHaveValue('한도에 막힌 이름') // 되돌아가지 않는다
  expect(await page.evaluate(async () => (await fetch('/api/try/session/')).json())).toBeNull()

  await page.reload() // 편집은 새로고침을 넘겨 남는다(#197)
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
  await page.locator('[data-node-id="general"]').click()
  await expect(page.locator('aside[aria-label="노드 설정"]').getByLabel('이름')).toHaveValue('한도에 막힌 이름')
})
