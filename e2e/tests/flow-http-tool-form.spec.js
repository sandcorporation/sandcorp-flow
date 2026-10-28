const fs = require('fs')
const path = require('path')
const { test, expect, request } = require('@playwright/test')
const { provisionTenant, openMemberSession } = require('./memberToken')
const { trackRequests } = require('./settle')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'
const SHOTS = process.env.SHOTS_DIR // 있으면 증거 스크린샷을 남긴다
const TAG = process.env.SHOT_TAG || 'after'

async function shot(page, name) {
  if (!SHOTS) return
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.screenshot({ path: path.join(SHOTS, `${TAG}-${name}.png`) })
}

// 넘침은 페이지만이 아니라 폼 안쪽(입력·표·가로 스크롤 컨테이너)까지 잰다.
async function overflowing(page) {
  return page.evaluate(() => {
    const bad = []
    const root = document.documentElement
    if (root.scrollWidth > root.clientWidth) bad.push(`page ${root.scrollWidth}>${root.clientWidth}`)
    for (const el of document.querySelectorAll('aside *, form *, fieldset, [class*="overflow"]')) {
      if (el.closest('.react-flow')) continue
      // 입력란은 긴 글이 안에서 스크롤되는 게 정상 — 상자 자체가 화면 밖으로 나가는지만 본다.
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
        if (el.getBoundingClientRect().right > window.innerWidth) bad.push(`${el.tagName}#${el.id} 상자가 화면 밖`)
        continue
      }
      if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).display !== 'inline') bad.push(`${el.tagName}.${String(el.className).slice(0, 40)} ${el.scrollWidth}>${el.clientWidth}`)
    }
    return bad
  })
}

test.describe('HTTP 도구 노드 폼 — 실제 스택 (#123)', () => {
  test('노드 추가 → 허용 밖 주소 경고 → 도메인 등록 뒤 경고 사라짐 → 저장·새로고침 유지 → 390폭 넘침 없음', async ({ page }) => {
    test.setTimeout(120_000)
    const tenant = await provisionTenant(`Http Form ${Date.now()}`)
    const host = `api.http-form-${Date.now()}.example.org`
    const api = await request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${tenant.token}` } })
    await openMemberSession(page, ADMIN_URL, tenant.token, 'tenant/flows')

    await page.getByLabel('새 흐름 이름').fill('주문 조회 연동')
    await page.getByRole('button', { name: '만들기' }).click()
    await page.waitForURL(/\/tenant\/flows\/[0-9a-f-]{36}$/)
    const editorUrl = page.url()
    await page.getByRole('button', { name: '시작', exact: true }).click()
    await page.getByRole('button', { name: 'HTTP 도구', exact: true }).click()
    await expect(page.locator('.react-flow__node')).toHaveCount(2)
    // 실패 갈래 핸들이 캔버스에 있다
    await expect(page.locator('[data-node-id="http_tool_1"] [data-exit="next"]')).toBeVisible()
    await expect(page.locator('[data-node-id="http_tool_1"] [data-exit="error"]')).toBeVisible()
    await page.locator('[data-node-id="http_tool_1"]').click()

    await expect(page.getByText('시험 대화도 이 API를 실제로 호출합니다')).toBeVisible()
    await expect(page.getByText(/등록된 비밀이 없습니다/)).toBeVisible()

    // 허용 목록 밖 주소 → 경고 + 설정 탭 링크
    await page.getByLabel('주소(https)').fill(`https://${host}/orders/{{state.fields.order_id}}`)
    await page.getByLabel('주소(https)').blur()
    const warn = page.getByRole('status').filter({ hasText: '허용 도메인에 없습니다' })
    await expect(warn).toContainText(host)
    await expect(warn.getByRole('link', { name: '설정에서 허용 도메인 추가' })).toHaveAttribute('href', '/tenant/config?section=secrets')

    // 메서드·헤더(등록 안 된 비밀)·본문·save 행
    await page.getByLabel('메서드').selectOption('POST')
    await page.getByRole('button', { name: '헤더 추가' }).click()
    await page.getByLabel('헤더 이름').fill('Authorization')
    await page.getByLabel('헤더 값').fill('Bearer {{secret.shop_key}}')
    await page.getByLabel('헤더 값').blur()
    await expect(page.getByRole('status').filter({ hasText: '등록되지 않은 비밀' })).toContainText('shop_key')
    await page.getByLabel('본문 틀').fill('{"q":"{{state.user_message}}"}')
    await page.getByLabel('본문 틀').blur()
    await page.getByRole('button', { name: '저장할 값 추가' }).click()
    await page.getByLabel('상태 이름').fill('order_status')
    await page.getByLabel('응답 경로').fill('$.data.status')
    await page.getByLabel('응답 경로').blur()
    await expect(page.getByRole('status').filter({ hasText: '초안 저장됨' })).toBeVisible()
    await page.getByLabel('주소(https)').scrollIntoViewIfNeeded()
    await warn.scrollIntoViewIfNeeded()
    await shot(page, 'http-warn-light')

    // 다른 탭에서 도메인·비밀을 등록하고 돌아오면(포커스) 경고가 사라진다
    expect((await api.post('/api/tenant/allowed-domains/', { data: { pattern: host } })).status()).toBe(201)
    expect((await api.put('/api/tenant/secrets/shop_key', { data: { value: 'sv-e2e-value-123456' } })).ok()).toBeTruthy()
    const net = trackRequests(page)
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await expect(page.getByRole('status').filter({ hasText: '허용 도메인에 없습니다' })).toHaveCount(0)
    await expect(page.getByRole('status').filter({ hasText: '등록되지 않은 비밀' })).toHaveCount(0)
    await expect(page.getByText(/등록된 비밀:/)).toContainText('shop_key')
    await net.idle()
    await expect(page.getByRole('status').filter({ hasText: '초안 저장됨' })).toBeVisible()

    // 새로고침해도 유지
    await page.reload()
    await expect(page.locator('.react-flow__node')).toHaveCount(2)
    await page.locator('[data-node-id="http_tool_1"]').click()
    await expect(page.getByLabel('주소(https)')).toHaveValue(`https://${host}/orders/{{state.fields.order_id}}`)
    await expect(page.getByLabel('메서드')).toHaveValue('POST')
    await expect(page.getByLabel('헤더 값')).toHaveValue('Bearer {{secret.shop_key}}')
    await expect(page.getByLabel('본문 틀')).toHaveValue('{"q":"{{state.user_message}}"}')
    await expect(page.getByLabel('상태 이름')).toHaveValue('order_status')
    await expect(page.getByLabel('응답 경로')).toHaveValue('$.data.status')
    await expect(page.getByRole('status').filter({ hasText: '허용 도메인에 없습니다' })).toHaveCount(0)

    // 다크
    await page.evaluate(() => localStorage.setItem('admin_theme', 'dark'))
    await page.reload()
    await page.locator('[data-node-id="http_tool_1"]').click()
    await expect(page.getByLabel('응답 경로')).toBeVisible()
    await page.getByLabel('메서드').scrollIntoViewIfNeeded()
    await page.getByLabel('응답 경로').scrollIntoViewIfNeeded()
    await shot(page, 'http-form-dark')
    await page.evaluate(() => localStorage.setItem('admin_theme', 'light'))

    // 390폭: 경고가 보이는 상태(허용 도메인을 지운 뒤)에서 폼 안쪽까지 넘침 없음
    const domains = await (await api.get('/api/tenant/allowed-domains/')).json()
    for (const d of domains) await api.delete(`/api/tenant/allowed-domains/${d.id}`)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto(editorUrl)
    await page.locator('[data-node-id="http_tool_1"]').click()
    await expect(page.getByRole('status').filter({ hasText: '허용 도메인에 없습니다' })).toBeVisible()
    await page.getByLabel('응답 경로').scrollIntoViewIfNeeded()
    expect(await overflowing(page)).toEqual([])
    await page.getByRole('status').filter({ hasText: '허용 도메인에 없습니다' }).scrollIntoViewIfNeeded()
    await shot(page, 'http-form-390')
    await api.dispose()
  })
})
