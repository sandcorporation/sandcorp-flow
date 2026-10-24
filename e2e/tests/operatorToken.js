/**
 * E2E Operator 자격증명(#21). Operator 비밀번호 로그인은 없다 — 로그인은 sandcorp-auth 콜백뿐이다.
 * api-e2e가 시작할 때 `manage.py seed_e2e_operator`가 대표 User·Operator를 시드하고
 * Flow 세션 Access Token을 공유 볼륨 파일(E2E_OPERATOR_TOKEN_FILE)에 써 둔다.
 */
const fs = require('fs')

const TOKEN_FILE = process.env.E2E_OPERATOR_TOKEN_FILE || '/e2e-shared/operator_token'

function operatorToken() {
  return fs.readFileSync(TOKEN_FILE, 'utf8').trim()
}

/** 관리 화면을 Operator로 연 상태로 만든다(access는 sessionStorage `op_access`, ADR-0013). */
async function openOperatorSession(page, adminUrl) {
  const token = operatorToken()
  await page.addInitScript((t) => sessionStorage.setItem('op_access', t), token)
  await page.goto(`${adminUrl}/admin-ui/operator`)
}

module.exports = { operatorToken, openOperatorSession }
