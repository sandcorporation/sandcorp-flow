const { test, expect } = require('@playwright/test')
const fs = require('fs')
const path = require('path')
const { operatorToken } = require('./operatorToken')
const { memberToken } = require('./memberToken')
const ROUTES = require('./narrow-routes.json')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const SHOTS = process.env.SHOTS_DIR // 있으면 화면마다 스크린샷을 남긴다(증거용)

// 390폭에서 표·셸이 가로로 잘리거나 밀리지 않는다(#90). 기준은 페이지가 아니라 모든 요소다 —
// 표 래퍼(overflow-auto) 안쪽에서 가로 스크롤이 생기면 페이지는 0이어도 내용이 잘리기 때문이다.
// 표마다 긴 이름·작업 버튼이 많은 행을 일부러 넣어 빈 표로 통과하지 못하게 한다.
const LONG = '아주긴고객사이름주식회사샌드코퍼레이션글로벌솔루션즈-2026-서울지점-LongNameWithoutSpaces'
const PARAMS = { ':visitorId': 'v-long-0001', ':sessionId': 's-long-0001', ':escalationId': 'e-long-0001', ':id': 'f-long-0001' }
const T0 = '2026-10-01T09:00:00Z'

const FLOW_NODES = [
  { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 0 }, config: {} },
  { id: 'ai_1', kind: 'ai_answer', code: 'N-02', name: LONG, position: { x: 240, y: 0 }, config: { instructions: LONG } },
  { id: 'guard_1', kind: 'guardrail', code: 'N-03', name: '검사', position: { x: 480, y: 0 }, config: { rules: ['pii'], action: 'block', target: 'output' } },
  { id: 'human_1', kind: 'handoff', code: 'N-04', name: '상담원', position: { x: 720, y: 0 }, config: { reason: '' } },
]
const FLOW_OUT = { id: 'f-long-0001', name: LONG, is_public: false, deployed_version: 2, updated_at: T0, has_pending_request: false }

const REQ_OUT = { id: 'r-long-0001', flow_id: 'f-long-0001', flow_name: LONG, version: 2, status: 'pending', requested_by_name: LONG, decided_by_name: null, decided_at: null, decision_note: '', created_at: T0 }
const VERSIONS = [
  { number: 2, note: LONG, created_by_name: LONG, created_at: T0, deployed: true, hash: 'h2' },
  { number: 1, note: 'https://example.com/' + 'a'.repeat(120), created_by_name: '김관리', created_at: T0, deployed: false, hash: 'h1' },
]

const TRY_SESSION = { flow_id: 'f-long-0001', template: 'shop_support', turns_remaining: 12, expires_at: '2026-12-01T00:00:00Z' }
const FIXTURES = [
  // 공개 흐름 보기 /flows/:slug (#39) — 긴 브랜드·흐름 이름으로 머리줄·안내 줄이 넘치지 않는지 본다.
  [/\/api\/public\/flows\/[^/]+\/$/, { slug: 'f-long-0001', brand_name: LONG, flow_name: LONG, version: 2, template: 'sandcorp_home', graph: { nodes: FLOW_NODES, edges: [{ from: 'start', to: 'ai_1' }] } }],
  // 공개 체험 /try (#37) — 긴 이름·긴 문서명으로 카드와 머리줄이 넘치지 않는지 본다.
  [/\/api\/try\/templates\/$/, ['sandcorp_home', 'shop_support', 'academy_reservation'].map((id) => ({ id, name: LONG, documents: [LONG, 'https://example.com/' + 'a'.repeat(80)] }))],
  [/\/api\/try\/templates\/[^/]+\/$/, { id: 'shop_support', name: LONG, documents: [LONG], graph: { nodes: FLOW_NODES, edges: [{ from: 'start', to: 'ai_1' }] } }],
  [/\/api\/try\/session\/$/, TRY_SESSION],
  [/\/api\/try\/flows\/[^/]+\/$/, {
    flow: FLOW_OUT,
    draft: { graph: { nodes: FLOW_NODES, edges: [{ from: 'start', to: 'ai_1' }] }, valid: true, errors: [] },
    versions: [], pending_request: null,
  }],
  [/\/api\/tenant\/flows\/$/, [{ ...FLOW_OUT, has_pending_request: true }]],
  [/\/api\/tenant\/deploy-requests\/$/, [REQ_OUT]],
  [/\/api\/tenant\/members\/me$/, { id: 'm1', tenant_id: 't1', tenant_name: '고객사', role: 'admin', name: '김관리', email: 'a@example.com' }],
  [/\/api\/tenant\/flows\/[^/]+\/$/, {
    flow: FLOW_OUT,
    draft: { graph: { nodes: FLOW_NODES, edges: [{ from: 'start', to: 'ai_1' }] }, valid: false, errors: [{ node_id: 'guard_1', code: 'unreachable_node', message: LONG }] },
    versions: VERSIONS, pending_request: REQ_OUT,
  }],
  [/\/api\/operator\/tenants\/$/, [
    { id: 't1', name: LONG, is_active: true, created_at: T0, active_admin_count: 0 },
    { id: 't2', name: '정지된 고객사', is_active: false, created_at: T0, active_admin_count: 1 },
    { id: 't3', name: '보통 고객사', is_active: true, created_at: T0, active_admin_count: 2 },
  ]],
  [/\/api\/operator\/usage\/$/, {
    total_tokens: 123456789, cost_usd: '12345.678901', unpriced_models: [LONG, 'another-unknown-model'], unpriced_requests: 3,
    by_tenant: [{ tenant_id: 't1', tenant_name: LONG, total_tokens: 98765432, request_count: 12345, cost_usd: '12345.678901', unpriced_models: [LONG], unpriced_requests: 3 }],
    daily: [{ date: '2026-10-01', total_tokens: 1000, cost_usd: '1234.567890', has_unpriced: true }, { date: '2026-10-02', total_tokens: 2000, cost_usd: '0.000000', has_unpriced: false }],
  }],
  [/\/api\/tenant\/usage\/$/, {
    total_tokens: 123456789, cost_usd: '12345.678901', unpriced_models: [LONG], unpriced_requests: 3,
    by_call_type: [{ call_type: 'chat_completion_with_a_very_long_call_type_name', input_tokens: 12345678, output_tokens: 87654321, total_tokens: 99999999, request_count: 123456, cost_usd: '12345.678901', unpriced_models: [LONG], unpriced_requests: 3 }],
    daily: [{ date: '2026-10-01', total_tokens: 1000, cost_usd: '1234.567890', has_unpriced: true }, { date: '2026-10-02', total_tokens: 2000, cost_usd: '0.000000', has_unpriced: false }],
  }],
  [/\/api\/tenant\/documents\/$/, [
    { id: 'd1', name: LONG + '.pdf', status: 'ready', error_message: '' },
    { id: 'd2', name: '실패한 문서.pdf', status: 'failed', error_message: '텍스트를 추출하지 못했습니다 — 스캔본인지 확인하세요' },
  ]],
  [/\/api\/tenant\/members\/$/, [
    { id: 'm1', user_id: 'u1', name: LONG, email: 'a-very-long-email-address-for-testing@example-company-domain.co.kr', sub_tail: 'ABCDEF', is_active: true, role: 'admin' },
    { id: 'm2', user_id: 'u2', name: '팀원 둘', email: 'm2@example.com', sub_tail: 'GHIJKL', is_active: true, role: 'member' },
    { id: 'm3', user_id: 'u3', name: '비활성 팀원', email: null, sub_tail: 'MNOPQR', is_active: false, role: 'member' },
  ]],
  [/\/api\/tenant\/invites\/$/, [{ id: 'i1', role: 'member', expires_at: '2026-12-01T00:00:00Z', created_at: T0 }]],
  [/\/api\/tenant\/visitors\/$/, [{ visitor_id: 'v-long-0001' }, { visitor_id: 'visitor-' + 'x'.repeat(60) }]],
  [/\/api\/tenant\/visitors\/[^/]+\/sessions\/$/, [{ session_id: 's-long-0001', visitor_id: 'v-long-0001', is_hitl: true, created_at: T0 }]],
  [/\/api\/tenant\/visitors\/[^/]+\/memory\/$/, [
    { id: 'mem1', key: 'preferred_contact_channel_and_time', value: LONG + LONG },
    { id: 'mem2', key: '이름', value: '홍길동' },
  ]],
  [/\/api\/tenant\/sessions\/[^/]+\/messages\/$/, [
    { id: 'x1', role: 'user', content: LONG + LONG, created_at: T0 },
    { id: 'x2', role: 'assistant', content: 'https://example.com/' + 'a'.repeat(120), created_at: T0 },
  ]],
  [/\/api\/tenant\/escalations\/[^/]+\/messages$/, Array.from({ length: 12 }, (_, i) => ({ id: `em${i}`, role: i % 2 ? 'human_agent' : 'user', content: `긴 대화 ${i} ` + LONG, created_at: T0 }))],
  [/\/api\/tenant\/escalations\/$/, [{ id: 'e-long-0001', session_id: 's-long-0001', trigger_type: 'user_request', reason: LONG, status: 'pending', created_at: T0 }]],
]

async function mockApi(page) {
  await page.route(/\/api\/(operator|tenant|try|public)\//, (route) => {
    const req = route.request()
    if (req.method() !== 'GET') return route.fallback()
    const { pathname } = new URL(req.url())
    const hit = FIXTURES.find(([re]) => re.test(pathname))
    if (!hit) return route.fallback()
    return route.fulfill({ json: hit[1] })
  })
}

/** 넘치는 요소: 안쪽 내용이 자기 폭보다 넓은데 보이게 두었거나(clip 아님) 스크롤 컨테이너인 것. 문서 자체도 본다. */
async function overflowing(page) {
  return page.evaluate(() => {
    const out = []
    const de = document.documentElement
    if (de.scrollWidth > de.clientWidth) out.push(`html ${de.scrollWidth}>${de.clientWidth}`)
    for (const el of document.body.querySelectorAll('*')) {
      if (el.closest('svg')) continue // 차트 글자는 svg 안에서 따로 그려진다
      if (el.closest('.react-flow')) continue // 편집기 캔버스 안은 이동·확대되는 좌표계 — 자르는 컨테이너(overflow-hidden)가 폭을 막고, 그 바깥은 문서 검사가 본다(#29)
      if (el.scrollWidth > el.clientWidth && el.clientWidth > 0 && getComputedStyle(el).display !== 'inline') {
        const r = el.getBoundingClientRect()
        if (r.width === 0 || getComputedStyle(el).visibility === 'hidden') continue
        out.push(`${el.tagName.toLowerCase()}${el.className && typeof el.className === 'string' ? '.' + el.className.split(' ').slice(0, 3).join('.') : ''} ${el.scrollWidth}>${el.clientWidth} «${(el.textContent || '').trim().slice(0, 30)}»`)
      }
    }
    return out
  })
}

const targets = []
for (const [role, list] of Object.entries(ROUTES)) {
  if (role.startsWith('_')) continue
  for (const r of list) targets.push({ role, route: r })
}

for (const theme of ['light', 'dark']) {
  test.describe(`390폭 가로 넘침 없음 — ${theme}`, () => {
    test.use({ viewport: { width: 390, height: 844 } })
    for (const { role, route } of targets) {
      test(`${role}/${route}`, async ({ page }) => {
        const url = route.split('/').map((s) => PARAMS[s] ?? s).join('/')
        const pub = role === 'public' // 로그인 없는 화면 — 토큰을 심지 않는다
        const key = role === 'operator' ? 'op_access' : 'agent_access'
        const token = pub ? '' : role === 'operator' ? operatorToken() : memberToken()
        await page.addInitScript(([k, t, th]) => {
          if (t) sessionStorage.setItem(k, t)
          localStorage.setItem('admin_theme', th)
        }, [key, token, theme])
        await mockApi(page)
        await page.goto(`${ADMIN_URL}/admin-ui/${pub ? '' : role + '/'}${url}`)
        await page.waitForLoadState('load') // HITL 은 SSE 가 열려 있어 networkidle 이 오지 않는다
        await page.waitForTimeout(800)
        if (SHOTS) {
          fs.mkdirSync(SHOTS, { recursive: true })
          await page.screenshot({ path: path.join(SHOTS, `${process.env.SHOT_TAG || 'after'}-${theme}-${role}-${route.replace(/[/:]/g, '_')}.png`), fullPage: true })
        }
        expect(await overflowing(page)).toEqual([])
      })
    }
  })
}

// 설정 탭 줄에 세로 스크롤바가 생기지 않는다(#90). 가로 넘침 검사는 세로를 보지 않으므로 따로 둔다.
test('설정 탭 줄: 세로 넘침 0', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript((t) => sessionStorage.setItem('agent_access', t), memberToken())
  await mockApi(page)
  await page.goto(`${ADMIN_URL}/admin-ui/tenant/config`)
  const tabs = page.getByRole('tablist').first()
  await expect(tabs).toBeVisible()
  const m = await tabs.evaluate((el) => ({ sh: el.scrollHeight, ch: el.clientHeight, oy: getComputedStyle(el).overflowY }))
  expect(m.sh).toBeLessThanOrEqual(m.ch)
  expect(m.oy).not.toBe('scroll')
})

// HITL 화면을 열어도 페이지가 아래로 끌려가지 않는다(#90): 대화 로그 scrollIntoView 가 페이지까지 올리던 결함.
test('HITL 진입: window.scrollY == 0', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript((t) => sessionStorage.setItem('agent_access', t), memberToken())
  await mockApi(page)
  await page.goto(`${ADMIN_URL}/admin-ui/tenant/hitl`)
  await expect(page.getByText('긴 대화 0').first()).toBeVisible()
  await page.waitForTimeout(1200) // smooth 스크롤이 끝날 시간
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
  expect(await page.evaluate(() => document.querySelector('main').scrollTop)).toBe(0)
})

// 페이지(window)뿐 아니라 모든 요소의 scrollTop 을 본다(#106): #103 이 셸을 뷰포트 높이로 고정해 <main> 이 스크롤 컨테이너가 됐다.
// 로그 컨테이너([data-log-scroll])만 아래로 가도 된다. 카드가 여럿이라 main 이 넘치는데 로그는 안 넘치는 경우(대화가 짧음)와
// 로그가 넘치는 경우를 함께 만든다.
const SHORT_ESCS = Array.from({ length: 6 }, (_, i) => ({ id: `e-s${i}`, session_id: `s-s${i}`, trigger_type: 'user_request', reason: `짧은 사유 ${i}`, status: 'pending', created_at: T0 }))
async function scrolledAncestors(page) {
  return page.evaluate(() => {
    const out = []
    if (window.scrollY > 0) out.push(`window ${window.scrollY}`)
    for (const el of document.querySelectorAll('body *')) {
      if (el.scrollTop > 0 && !el.closest('[data-log-scroll]')) out.push(`${el.tagName}.${String(el.className).slice(0, 30)} ${el.scrollTop}`)
    }
    return out
  })
}
for (const [name, size] of [['390', { width: 390, height: 844 }], ['데스크톱', { width: 1280, height: 720 }]]) {
  for (const short of [false, true]) {
    test(`HITL 진입(${name}, ${short ? '짧은 대화' : '긴 대화'}): 모든 스크롤 조상 scrollTop 0`, async ({ page }) => {
      await page.setViewportSize(size)
      await page.addInitScript((t) => sessionStorage.setItem('agent_access', t), memberToken())
      await mockApi(page)
      if (short) {
        await page.route((u) => /\/api\/tenant\/escalations\/$/.test(u.pathname), (r) => r.request().method() === 'GET' ? r.fulfill({ json: SHORT_ESCS }) : r.fallback())
        await page.route((u) => /\/api\/tenant\/escalations\/[^/]+\/messages$/.test(u.pathname), (r) => r.fulfill({ json: [{ id: 'q1', role: 'user', content: '안녕', created_at: T0 }] }))
      }
      await page.goto(`${ADMIN_URL}/admin-ui/tenant/hitl`)
      await expect(page.getByRole('heading', { name: /상담 콘솔/ })).toBeVisible()
      await page.waitForTimeout(1500)
      const mainOverflows = await page.evaluate(() => { const m = document.querySelector('main'); const d = document.documentElement; return m.scrollHeight > m.clientHeight || d.scrollHeight > d.clientHeight })
      if (short) expect(mainOverflows).toBe(true) // 시험이 의미를 가지려면 main 또는 문서가 스크롤 가능해야 한다
      if (SHOTS) {
        fs.mkdirSync(SHOTS, { recursive: true })
        await page.screenshot({ path: path.join(SHOTS, `${process.env.SHOT_TAG || 'after'}-hitl-${name}-${short ? 'short' : 'long'}.png`) })
      }
      const scrolled = await scrolledAncestors(page)
      if (SHOTS) console.log('SCROLLED', name, short ? 'short' : 'long', JSON.stringify(scrolled))
      expect(scrolled).toEqual([])
      await expect(page.getByRole('heading', { name: /상담 콘솔/ })).toBeInViewport()
    })
  }
}
