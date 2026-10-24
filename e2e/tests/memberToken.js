/**
 * E2E 멤버(고객) 자격증명(#56). 고객 로그인도 sandcorp-auth 콜백뿐이라 시드를 쓴다.
 * api-e2e가 시작할 때 `manage.py seed_e2e_member`가 멤버·조직·Admin 멤버십을 시드하고
 * Flow 세션 Access Token을 공유 볼륨에 써 둔다(member_token). 초대 수락 대역은 invite_token 파일에
 * 초대 원문이 떨어지길 기다렸다가 수락하고 invitee_token을 쓴다.
 */
const fs = require('fs')
const path = require('path')

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

/** 관리 화면을 고객(멤버)으로 연 상태로 만든다(access는 sessionStorage `agent_access`, ADR-0013). */
async function openMemberSession(page, adminUrl, token, route = 'tenant') {
  await page.addInitScript((t) => sessionStorage.setItem('agent_access', t), token)
  await page.goto(`${adminUrl}/admin-ui/${route}`)
}

module.exports = { memberToken, inviteeToken, dropInvite, openMemberSession, takeOnboardingTicket }
