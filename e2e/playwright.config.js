const { defineConfig } = require('@playwright/test')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'

module.exports = defineConfig({
  testDir: './tests',
  timeout: 3_000, // 시험 하나(앞뒤 걸이 포함) 3초. 스펙에서 test.setTimeout/slow 로 올리지 않는다(#257)
  retries: 0,
  // 워커가 많으면 한 시험의 시간이 부풀어 3초 한도 근처에서 번갈아 떨어진다(Docker Desktop 14코어 실측: 7워커 23건 >2초, 4워커 1건 실패). E2E_WORKERS 로 조정.
  workers: Number(process.env.E2E_WORKERS) || 4,
  globalSetup: require.resolve('./global-setup'),
  use: {
    headless: true,
    baseURL: ADMIN_URL,
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
})

// Export URLs for use in tests
module.exports.ADMIN_URL = ADMIN_URL
module.exports.WIDGET_URL = WIDGET_URL
module.exports.API_URL = API_URL
