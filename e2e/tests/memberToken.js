/**
 * E2E 멤버(고객) 자격증명(#56). 고객 로그인도 sandcorp-auth 콜백뿐이라 시드를 쓴다.
 * api-e2e가 시작할 때 `manage.py seed_e2e_member`가 멤버·조직·Admin 멤버십을 시드하고
 * Flow 세션 Access Token을 공유 볼륨에 써 둔다(member_token). 초대 수락 대역은 invite_token 파일에
 * 초대 원문이 떨어지길 기다렸다가 수락하고 invitee_token을 쓴다.
 */
const fs = require('fs')
const path = require('path')
const { test } = require('@playwright/test')

const DIR = process.env.E2E_SHARED_DIR || '/e2e-shared'

const read = (name) => fs.readFileSync(path.join(DIR, name), 'utf8').trim()
const memberToken = () => read('member_token')
const inviteeToken = () => read('invitee_token')
/** 초대 원문을 수락 대역에 넘긴다(sandcorp-auth 로그인 → 콜백의 accept_invite 자리). */
const dropInvite = (raw) => fs.writeFileSync(path.join(DIR, 'invite_token'), raw)

/** 멤버십 없는 새 사용자의 콜백 표(member_ticket)를 가져간다 — 가져가면 시드가 다음 사람 것을 채운다(#41). */
async function takeOnboardingTicket(timeoutMs = 15000) {
  const file = path.join(DIR, 'onboarding_ticket')
  const until = Date.now() + timeoutMs
  while (Date.now() < until) {
    if (fs.existsSync(file)) {
      const raw = fs.readFileSync(file, 'utf8').trim()
      fs.unlinkSync(file)
      if (raw) return raw
    }
    await new Promise((r) => setTimeout(r, 300))
  }
  throw new Error('onboarding_ticket이 시드되지 않았습니다(api-e2e 로그 확인)')
}

/**
 * 스펙 전용 새 조직과 그 Admin 멤버 토큰을 받는다(#65). 조직 이름을 요청 파일로 떨구면 시드(--serve-tenants-from)가
 * 조직+Admin 멤버십을 만들고 토큰을 쓴다. 반환: { name, token, secondToken }.
 */
async function provisionTenant(name, timeoutMs = 15000, { defaultFlow = false } = {}) {
  const dir = path.join(DIR, 'tenant_requests')
  fs.mkdirSync(dir, { recursive: true })
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  // 조직 이름은 대소문자 무시 유일(uq_tenant_name_ci)이다. `--repeat-each`로 같은 시험이 나란히 돌면 `Date.now()`만으로는
  // 같은 ms 에 이름이 겹쳐 시드 대역이 UniqueViolation 으로 죽고, 그 뒤 모든 조직 요청이 시간 초과한다(#128).
  // 반복 판에서만 반복 번호를 붙인다 — 보통 판의 이름 길이는 그대로 둔다(좁은 폭 넘침 시험이 이름 길이에 민감하다).
  const repeat = test.info().repeatEachIndex
  if (repeat > 0) name = `${name} r${repeat}`
  fs.writeFileSync(path.join(dir, `${id}.req.tmp`), defaultFlow ? `${name}\ndefault-flow` : name)
  fs.renameSync(path.join(dir, `${id}.req.tmp`), path.join(dir, `${id}.req`))
  const tokenFile = path.join(dir, `${id}.token`)
  const until = Date.now() + timeoutMs
  while (Date.now() < until) {
    if (fs.existsSync(tokenFile)) {
      // 같은 조직의 두 번째 Admin(배포 승인은 요청자와 다른 Admin 이 한다, #31) — 첫 토큰보다 먼저 쓰여 있다.
      const second = path.join(dir, `${id}.admin2.token`)
      return { name, token: fs.readFileSync(tokenFile, 'utf8').trim(), secondToken: fs.existsSync(second) ? fs.readFileSync(second, 'utf8').trim() : null }
    }
    await new Promise((r) => setTimeout(r, 200))
  }
  throw new Error('조직 요청이 처리되지 않았습니다(api-e2e 로그 확인)')
}

/** 관리 화면을 고객(멤버)으로 연 상태로 만든다(access는 sessionStorage `agent_access`, ADR-0013). */
async function openMemberSession(page, adminUrl, token, route = 'tenant') {
  await page.addInitScript((t) => sessionStorage.setItem('agent_access', t), token)
  await page.goto(`${adminUrl}/admin-ui/${route}`)
}

module.exports = { memberToken, inviteeToken, dropInvite, openMemberSession, takeOnboardingTicket, provisionTenant }
