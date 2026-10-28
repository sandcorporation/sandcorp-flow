/**
 * Playwright global setup: API가 준비되고 시드한 Operator 세션이 통하는지 확인한다.
 * Operator 비밀번호 로그인은 없다(#21) — api-e2e가 시작할 때 `manage.py seed_e2e_operator`가
 * 대표 User·Operator를 시드하고 Flow 세션 Access Token을 공유 볼륨 파일에 써 둔다.
 */
const { request } = require('@playwright/test')
const { operatorToken } = require('./tests/operatorToken')

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
