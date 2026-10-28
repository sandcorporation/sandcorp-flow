/**
 * Playwright global setup: API가 준비되고 시드한 Operator 세션이 통하는지 확인한다.
 * Operator 비밀번호 로그인은 없다(#21) — api-e2e가 시작할 때 `manage.py seed_e2e_operator`가
 * 대표 User·Operator를 시드하고 Flow 세션 Access Token을 공유 볼륨 파일에 써 둔다.
 */
const { request } = require('@playwright/test')
const fs = require('fs')
const path = require('path')
const { chromium } = require('@playwright/test')
const { operatorToken } = require('./tests/operatorToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'

/**
 * Vite dev 서버는 모듈을 처음 요청받을 때 변환한다 — 화면을 처음 여는 시험 하나가 혼자 그 비용(1~2초)을 내서
 * 3초 한도 근처에서 시험이 번갈아 떨어진다(#257). 시험 시간 밖(전역 준비)에서 주요 화면을 한 번씩 열어 미리 치른다.
 */
async function warmDevServers() {
  const memberFile = path.join(process.env.E2E_SHARED_DIR || '/e2e-shared', 'member_token')
  const browser = await chromium.launch()
  try {
    const context = await browser.newContext()
    const member = fs.existsSync(memberFile) ? fs.readFileSync(memberFile, 'utf8').trim() : ''
    await context.addInitScript(([m, o]) => {
      if (m) sessionStorage.setItem('agent_access', m)
      sessionStorage.setItem('op_access', o)
    }, [member, operatorToken()])
    const page = await context.newPage()
    const urls = [
      ...['', '/tenant', '/tenant/flows', '/tenant/flows/00000000-0000-0000-0000-000000000000', '/tenant/documents', '/tenant/config',
        '/tenant/usage', '/tenant/visitors', '/tenant/hitl', '/tenant/graph', '/tenant/agents',
        '/operator', '/operator/tenants', '/operator/usage', '/try', '/try/signup', '/try/claim'].map((r) => `${ADMIN_URL}/admin-ui${r}`),
      `${WIDGET_URL}/embed/chatbot/warm/`,
    ]
    for (const url of urls) {
      try {
        await page.goto(url, { waitUntil: 'networkidle', timeout: 20_000 }) // wait-ok: 예열 — 화면이 쓰는 모듈 요청이 잦아들 때까지(시험 시간 밖, 전역 준비)
      } catch (e) {
        // 예열일 뿐이다 — 화면이 안 열려도 시험이 스스로 말한다.
      }
    }
  } finally {
    await browser.close()
  }
}

const API_URL = process.env.API_URL || 'http://localhost:8000'

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
        await api.dispose()
        await warmDevServers()
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
