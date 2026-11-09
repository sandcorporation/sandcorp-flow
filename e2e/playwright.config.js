const { defineConfig } = require('@playwright/test')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const WIDGET_URL = process.env.WIDGET_URL || 'http://localhost:5173'
const API_URL = process.env.API_URL || 'http://localhost:8000'

// E2E_SHARD=i/N 이면 시험을 N 조각으로 나눠 i번째만 돈다(Actions 가 실행기 여러 대에 나눠 돌린다). 비면 전부.
const shardMatch = /^(\d+)\/(\d+)$/.exec(process.env.E2E_SHARD || '')
const shard = shardMatch ? { current: Number(shardMatch[1]), total: Number(shardMatch[2]) } : null

module.exports = defineConfig({
  testDir: './tests',
  timeout: 3_000, // 시험 하나(앞뒤 걸이 포함) 3초. 스펙에서 test.setTimeout/slow 로 올리지 않는다(#257)
  retries: 0,
  // 조각(--shard)을 파일이 아니라 시험 단위로 나눈다 — 파일 단위면 큰 파일이 한 조각에 몰려 137/70으로 갈렸다(#333).
  // 파일 안 시험도 워커에 흩어지므로 test.beforeAll 은 워커마다 다시 돈다(각자 새 테넌트를 만든다). 시험끼리 상태를 넘기지 않는다.
  fullyParallel: true,
  // 시험마다 걸린 시간을 남긴다 — 3초 한도 근처(2초 넘는) 시험을 CI 로그에서 바로 찾는다
  reporter: [['list']],
  // 워커가 많으면 한 시험의 시간이 부풀어 3초 한도 근처에서 번갈아 떨어진다(Docker Desktop 14코어 실측: 7워커 23건 >2초, 4워커 1건 실패). E2E_WORKERS 로 조정.
  workers: Number(process.env.E2E_WORKERS) || 4,
  shard,
  globalSetup: require.resolve('./global-setup'),
  use: {
    headless: true,
    baseURL: ADMIN_URL,
    // 영상은 기본으로 끈다 — retain-on-failure 도 모든 시험을 녹화(인코딩)한 뒤 버려서 4 vCPU 실행기에서 CPU 를 먹고,
    // CI 는 test-results 를 올리지 않아 아무도 보지 않았다(#343). 실패를 영상으로 볼 때만 E2E_VIDEO=retain-on-failure.
    video: process.env.E2E_VIDEO || 'off',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
})

// Export URLs for use in tests
module.exports.ADMIN_URL = ADMIN_URL
module.exports.WIDGET_URL = WIDGET_URL
module.exports.API_URL = API_URL
