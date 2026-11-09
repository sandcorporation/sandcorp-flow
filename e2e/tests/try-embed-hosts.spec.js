// 임베드 e2e 여정(#181): 같은 사이트 두 호스트 — 부모 portfolio.flowtest.test 가 iframe 으로 flow.flowtest.test 의 /try 를 품는다.
// 두 호스트 모두 admin-e2e(Vite)로 풀리고(Chromium --host-resolver-rules), eTLD+1 이 flowtest.test 로 같아 SameSite=Strict 체험 쿠키가 iframe 에서도 간다.
// 부모 HTML 은 page.route 로 서빙한다(스니펫은 docs/deployment.md 「임베드」 절의 것과 같다). 쓰기 Origin 검사를 위해 api-e2e 는 TRY_ALLOWED_ORIGINS 에 FLOW 호스트를 가진다.
// ④ 다른 사이트에서의 프레임 거부(frame-ancestors)는 e2e 스택(Vite)에 그 헤더가 없어 nginx 계약 시험(scripts/test-nginx-metrics.sh)이 맡는다.
const { test, expect } = require('@playwright/test')
const { settled } = require('./settle')

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

// ①-가 를 두 시험으로 나눈다(#343) — 한 시험에서 편집기를 두 번 띄우면 느린 실행기에서 3초 한도를 넘었다.
test('①-가 iframe 안 고쳐 보기: 곧바로 편집기(세션 없음) → 노드 바꾸기(세션 생김)', async ({ page }) => {
  const frame = await openParent(page)
  await expect(frame.locator('.react-flow__node').first()).toBeVisible()
  await expect(frame.getByRole('banner')).toHaveCount(0)
  expect(await sessionOf(page.frame({ url: /flow\.flowtest\.test/ }))).toBeNull() // 방문만으로는 세션을 만들지 않는다(#195)

  const aside = frame.locator('aside[aria-label="노드 설정"]')
  await frame.locator('[data-node-id="general"]').click()
  await aside.getByRole('tab', { name: '고급' }).click()
  const cfg = aside.locator('#node-config')
  const parsed = JSON.parse(await cfg.inputValue())
  parsed.instructions = '항상 한 문장으로 답한다. (임베드에서 고침)'
  await cfg.fill(JSON.stringify(parsed))
  await aside.getByRole('button', { name: '설정 적용' }).click()
  await expect(frame.getByText('초안 저장됨')).toBeVisible()
  const made = await sessionOf(page.frame({ url: /flow\.flowtest\.test/ }))
  expect(made?.flow_id).toBeTruthy()
  expect(made?.template).toBe('sandcorp_home')
})

test('①-가\' iframe 안에서 고친 세션: iframe 새로고침 뒤 같은 세션·고친 설정', async ({ page }) => {
  // 앞 단계(편집기에서 고쳐 세션 만들기)는 ①-가 가 화면으로 본다 — 여기선 iframe 안에서 같은 쓰기 API 로 만든다(쿠키는 iframe 의 것)
  await openParent(page)
  const inner = () => page.frame({ url: /flow\.flowtest\.test/ })
  await expect.poll(() => inner()?.url() ?? '').toContain('/admin-ui/try')
  const before = await inner().evaluate(async () => {
    const graph = (await (await fetch('/api/try/templates/sandcorp_home/')).json()).graph
    const general = graph.nodes.find((n) => n.id === 'general')
    general.config = { ...general.config, instructions: '항상 한 문장으로 답한다. (임베드에서 고침)' }
    const res = await fetch('/api/try/sessions/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ template: 'sandcorp_home', graph }) })
    return res.json()
  })
  expect(before?.flow_id).toBeTruthy()

  // iframe 만 새로고침 — 같은 세션, 고친 설정이 남는다
  await inner().evaluate(() => location.reload())
  const frame = page.frameLocator('#flow-try')
  await expect(frame.locator('.react-flow__node').first()).toBeVisible()
  await expect.poll(async () => (await sessionOf(inner()).catch(() => null))?.flow_id).toBe(before.flow_id)
  const aside = frame.locator('aside[aria-label="노드 설정"]')
  await frame.locator('[data-node-id="general"]').click()
  await aside.getByRole('tab', { name: '고급' }).click()
  await expect(aside.locator('#node-config')).toHaveValue(/임베드에서 고침/)
})

test('①-나 iframe 안 시험 대화: 한 턴을 보내면 답이 온다', async ({ page }) => {
  const frame = await openParent(page)
  await expect(frame.locator('.react-flow__node').first()).toBeVisible()
  const aside = frame.locator('aside[aria-label="노드 설정"]')
  await frame.locator('[data-node-id="general"]').click()
  await aside.getByRole('tab', { name: '시험 대화' }).click()
  await aside.getByLabel('시험 메시지').fill('안녕하세요')
  await aside.getByRole('button', { name: '보내기' }).click()
  const chat = aside.getByRole('region', { name: '시험 대화' })
  await expect(chat.locator('li').first().locator('[data-reply], :text("사람 넘김 노드에 닿았습니다")').first()).toBeVisible({ timeout: 90_000 })
})

test('② 세션 없는 iframe 에서 「이 흐름으로 가입」: 새 탭이 signup 을 거쳐 authorize 에 닿고, 그 뒤 iframe 의 세션이 새 탭과 같다', async ({ page, context }) => {
  // sandcorp-auth 왕복은 외부 경계 — 새 탭이 닿는 authorize 주소를 가로채 같은 호스트의 빈 문서로 대신한다
  await context.route(/\/api\/auth\/authorize\/member/, (route) =>
    route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>auth stub</title>' }))
  const frame = await openParent(page)
  await expect(frame.locator('.react-flow__node').first()).toBeVisible()
  const inFrame = () => sessionOf(page.frame({ url: /flow\.flowtest\.test/ }))
  expect(await inFrame()).toBeNull() // 아무것도 고치지 않았다 — 세션 없음(#195)
  const link = frame.getByRole('link', { name: '이 흐름으로 가입' })
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', 'noopener nofollow')

  const popupPromise = context.waitForEvent('page')
  await link.click()
  const popup = await popupPromise
  await popup.waitForURL(/\/api\/auth\/authorize\/member\?then=try_claim$/) // /try/signup 이 세션을 만든 뒤 로그인으로 replace
  expect(new URL(popup.url()).origin).toBe(FLOW) // 최상위 탭이 flow 호스트다(iframe 이 아니라)
  expect(await popup.evaluate(() => window.top === window && window.opener === null)).toBe(true)
  const inPopup = await sessionOf(popup)
  expect(inPopup?.flow_id).toBeTruthy()
  expect(await inFrame()).toEqual(inPopup) // 쿠키 공유 — iframe 이 새 탭과 같은 세션을 본다
  // 부모는 그대로
  await expect(page).toHaveURL(`${PARENT}/`)
})

test('③ 부모가 hello 뒤 height 를 받아 iframe 높이가 바뀐다', async ({ page }) => {
  const frame = await openParent(page)
  await expect(frame.locator('.react-flow__node').first()).toBeVisible()
  expect(await iframeHeight(page)).not.toBe(300)
  const heights = await page.evaluate(() => window.__heights)
  expect(heights.length).toBeGreaterThan(0)
  expect(await iframeHeight(page)).toBe(heights[heights.length - 1])
})

test('③\' hello 를 안 보내면 높이는 오지 않고 iframe 은 처음 높이 그대로', async ({ page }) => {
  const frame = await openParent(page, { hello: false })
  await expect(frame.locator('.react-flow__node').first()).toBeVisible()
  await settled(page.frame({ url: /flow\.flowtest\.test/ })) // iframe 안 화면이 가라앉은 뒤에도 높이 메시지가 없어야 한다
  await settled(page)
  expect(await page.evaluate(() => window.__heights.length)).toBe(0)
  expect(await iframeHeight(page)).toBe(300)
})
