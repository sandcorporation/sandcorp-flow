// 임베드 e2e 여정(#181): 같은 사이트 두 호스트 — 부모 portfolio.flowtest.test 가 iframe 으로 flow.flowtest.test 의 /try 를 품는다.
// 두 호스트 모두 admin-e2e(Vite)로 풀리고(Chromium --host-resolver-rules), eTLD+1 이 flowtest.test 로 같아 SameSite=Strict 체험 쿠키가 iframe 에서도 간다.
// 부모 HTML 은 page.route 로 서빙한다(스니펫은 docs/deployment.md 「임베드」 절의 것과 같다). 쓰기 Origin 검사를 위해 api-e2e 는 TRY_ALLOWED_ORIGINS 에 FLOW 호스트를 가진다.
// ④ 다른 사이트에서의 프레임 거부(frame-ancestors)는 e2e 스택(Vite)에 그 헤더가 없어 nginx 계약 시험(scripts/test-nginx-metrics.sh)이 맡는다.
const { test, expect } = require('@playwright/test')

const FLOW = 'http://flow.flowtest.test:5174'
const PARENT = 'http://portfolio.flowtest.test:9999'

test.use({
  launchOptions: {
    args: [
      '--host-resolver-rules=MAP flow.flowtest.test admin-e2e, MAP portfolio.flowtest.test admin-e2e',
      // 공개(가짜) 호스트의 부모가 사설망 iframe 을 막는 Chromium 규칙을 푼다(운영은 둘 다 공개 주소)
      '--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessSendPreflights,BlockInsecurePrivateNetworkRequests',
    ],
  },
})

const parentHtml = ({ hello }) => `<!doctype html><html><body>
<h1>portfolio</h1>
<iframe id="flow-try" src="${FLOW}/admin-ui/try?embed=1&template=home" title="Flow 체험" style="width:100%;border:0;height:300px"></iframe>
<script>
  window.__heights = []
  const f = document.getElementById('flow-try')
  const FLOW_ORIGIN = '${FLOW}'
  ${hello ? "f.addEventListener('load', () => f.contentWindow.postMessage({ type: 'sandcorp-flow:hello' }, FLOW_ORIGIN))" : ''}
  window.addEventListener('message', (e) => {
    if (e.origin !== FLOW_ORIGIN || e.source !== f.contentWindow) return
    if (e.data && e.data.type === 'sandcorp-flow:height') { window.__heights.push(e.data.height); f.style.height = e.data.height + 'px' }
  })
</script></body></html>`

async function openParent(page, { hello = true } = {}) {
  await page.route(`${PARENT}/**`, (route) => route.fulfill({ contentType: 'text/html', body: parentHtml({ hello }) }))
  await page.setViewportSize({ width: 1200, height: 900 })
  await page.goto(`${PARENT}/`)
  return page.frameLocator('#flow-try')
}

const iframeHeight = (page) => page.evaluate(() => document.getElementById('flow-try').getBoundingClientRect().height)
const sessionOf = (frame) => frame.evaluate(async () => (await fetch('/api/try/session/')).json())

test('① iframe 안 고쳐 보기: 시작 → 노드 바꾸기 → 시험 대화 → iframe 새로고침 뒤 같은 세션', async ({ page }) => {
  test.setTimeout(180_000)
  const frame = await openParent(page)
  await frame.getByRole('button', { name: '고쳐 보기 시작' }).click()
  await expect(frame.locator('.react-flow__node').first()).toBeVisible({ timeout: 30_000 })
  await expect(frame.getByRole('banner')).toHaveCount(0)
  const before = await sessionOf(page.frame({ url: /flow\.flowtest\.test/ }))
  expect(before?.flow_id).toBeTruthy()

  const aside = frame.locator('aside[aria-label="노드 설정"]')
  await frame.locator('[data-node-id="general"]').click()
  await aside.getByRole('tab', { name: '고급' }).click()
  const cfg = aside.locator('#node-config')
  const parsed = JSON.parse(await cfg.inputValue())
  parsed.instructions = '항상 한 문장으로 답한다. (임베드에서 고침)'
  await cfg.fill(JSON.stringify(parsed))
  await aside.getByRole('button', { name: '설정 적용' }).click()
  await expect(frame.getByText('초안 저장됨')).toBeVisible()

  await aside.getByRole('tab', { name: '시험 대화' }).click()
  await aside.getByLabel('시험 메시지').fill('안녕하세요')
  await aside.getByRole('button', { name: '보내기' }).click()
  const chat = aside.getByRole('region', { name: '시험 대화' })
  await expect(chat.locator('li').first().locator('[data-reply], :text("사람 넘김 노드에 닿았습니다")').first()).toBeVisible({ timeout: 90_000 })

  // iframe 만 새로고침 — 같은 세션, 고친 설정이 남는다
  await page.frame({ url: /flow\.flowtest\.test/ }).evaluate(() => location.reload())
  await expect(frame.locator('.react-flow__node').first()).toBeVisible({ timeout: 30_000 })
  await expect.poll(async () => (await sessionOf(page.frame({ url: /flow\.flowtest\.test/ })).catch(() => null))?.flow_id).toBe(before.flow_id)
  await frame.locator('[data-node-id="general"]').click()
  await aside.getByRole('tab', { name: '고급' }).click()
  await expect(aside.locator('#node-config')).toHaveValue(/임베드에서 고침/)
})

test('② 「이 흐름으로 가입」은 새 탭(최상위 flow 호스트)으로 열리고 그 탭의 세션이 같다', async ({ page, context }) => {
  test.setTimeout(120_000)
  // sandcorp-auth 왕복은 외부 경계 — 새 탭이 닿는 authorize 주소를 가로채 같은 호스트의 빈 문서로 대신한다
  await context.route(/\/api\/auth\/authorize\/member/, (route) =>
    route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>auth stub</title>' }))
  const frame = await openParent(page)
  await frame.getByRole('button', { name: '고쳐 보기 시작' }).click()
  await expect(frame.locator('.react-flow__node').first()).toBeVisible({ timeout: 30_000 })
  const inFrame = await sessionOf(page.frame({ url: /flow\.flowtest\.test/ }))

  const popupPromise = context.waitForEvent('page')
  await frame.getByRole('link', { name: '이 흐름으로 가입' }).click()
  const popup = await popupPromise
  await popup.waitForLoadState()
  expect(new URL(popup.url()).origin).toBe(FLOW) // 최상위 탭이 flow 호스트다(iframe 이 아니라)
  expect(await popup.evaluate(() => window.top === window && window.opener === null)).toBe(true)
  expect(await sessionOf(popup)).toEqual(inFrame) // 쿠키 공유 — 같은 세션
  // 부모는 그대로
  await expect(page).toHaveURL(`${PARENT}/`)
})

test('③ 부모가 hello 뒤 height 를 받아 iframe 높이가 바뀐다', async ({ page }) => {
  const frame = await openParent(page)
  await expect(frame.getByRole('button', { name: '고쳐 보기 시작' })).toBeVisible()
  expect(await iframeHeight(page)).not.toBe(300)
  const heights = await page.evaluate(() => window.__heights)
  expect(heights.length).toBeGreaterThan(0)
  expect(await iframeHeight(page)).toBe(heights[heights.length - 1])
})

test('③\' hello 를 안 보내면 높이는 오지 않고 iframe 은 처음 높이 그대로', async ({ page }) => {
  const frame = await openParent(page, { hello: false })
  await expect(frame.getByRole('button', { name: '고쳐 보기 시작' })).toBeVisible()
  await page.waitForTimeout(1000)
  expect(await page.evaluate(() => window.__heights.length)).toBe(0)
  expect(await iframeHeight(page)).toBe(300)
})
