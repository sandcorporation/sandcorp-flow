/**
 * Playwright global setup: API가 준비되고 시드한 Operator 세션이 통하는지 확인한다.
 * Operator 비밀번호 로그인은 없다(#21) — api-e2e가 시작할 때 `manage.py seed_e2e_operator`가
 * 대표 User·Operator를 시드하고 Flow 세션 Access Token을 공유 볼륨 파일에 써 둔다.
 */
const { request, chromium } = require('@playwright/test')
const { operatorToken } = require('./tests/operatorToken')

const API_URL = process.env.API_URL || 'http://localhost:8000'
const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'

/**
 * 판 첫머리 데우기(#343): 새 스택의 첫 시험들이 첫 화면 로드(api 워커의 첫 요청·admin/widget 첫 응답)에 3초를 넘겼다.
 * 시험이 가장 많이 여는 /try 편집기를 한 번 띄워 노드가 그려질 때까지(사건) 기다리고, api 워커마다 닿도록
 * 같은 요청을 나란히 몇 번 보낸다. widget 첫 화면도 한 번 연다. 시험 시간에는 들어가지 않는다.
 */
async function warmUp(api) {
  const hits = ['/api/try/templates/sandcorp_home/', '/api/try/node-schema/', '/api/try/session/']
  await Promise.all(Array.from({ length: 4 }, () => Promise.all(hits.map((h) => api.get(h).catch(() => null)))))
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage()
    await page.goto(`${ADMIN_URL}/admin-ui/try?template=home`)
    await page.locator('.react-flow__node').first().waitFor({ timeout: 30_000 })
    await page.goto(`${WIDGET_URL}/embed/landing.html`)
    await page.waitForLoadState('load')
  } finally {
    await browser.close()
  }
  console.log('[global-setup] warmed admin /try, widget, api')
}

module.exports = async function globalSetup() {
  const api = await request.newContext({ baseURL: API_URL })

  // 최대 30초 대기
  for (let i = 0; i < 15; i++) {
    try {
      const res = await api.get('/api/operator/tenants/', {
        headers: { Authorization: `Bearer ${operatorToken()}` },
      })
      if (res.ok()) {
        console.log('[global-setup] Operator session OK')
        await warmUp(api)
        await api.dispose()
        return
      }
    } catch (e) {
      // not ready yet
    }
    await new Promise(r => setTimeout(r, 2000))
  }

  await api.dispose()
  throw new Error('E2E global setup: seeded operator session not accepted after 30s')
}
