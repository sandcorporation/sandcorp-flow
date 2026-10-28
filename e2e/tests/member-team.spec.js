const { test, expect, request } = require('@playwright/test')
const { memberToken, inviteeToken, dropInvite, openMemberSession } = require('./memberToken')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'
const API_URL = process.env.API_URL || 'http://localhost:8000'

async function adminApi() {
  return request.newContext({ baseURL: API_URL, extraHTTPHeaders: { Authorization: `Bearer ${memberToken()}` } })
}

/** 수락 대역이 초대를 쓸 때까지 API로 기다린다 — 대기 초대 0, 초대받은 사람이 활성 멤버(화면을 새로고침하며 세지 않는다, #257). */
async function waitAccepted(api) {
  await expect.poll(async () => {
    const invites = await (await api.get('/api/tenant/invites/')).json()
    const members = await (await api.get('/api/tenant/members/')).json()
    const invitee = members.find((m) => m.name === 'E2E 초대받은 사람')
    return `${invites.length}|${invitee ? invitee.is_active : 'none'}`
  }, { timeout: 20_000 }).toBe('0|true')
  const members = await (await api.get('/api/tenant/members/')).json()
  return members.find((m) => m.name === 'E2E 초대받은 사람')
}

// 고객 로그인은 sandcorp-auth 콜백뿐이다(#40). 실제 sandcorp-auth를 부를 수 없으므로 로그인 직후 상태를
// 시드(seed_e2e_member)로 만들고, 초대 수락은 콜백의 accept_invite를 부르는 대역에 초대 원문을 넘겨 대신한다.
test.describe('팀원 탭 — 초대 링크 → 수락 → 목록 (#56)', () => {
  test('Admin이 초대 링크를 만들면 수락한 사람이 목록에 보이고, 새로고침해도 유지된다', async ({ page }) => {
    await openMemberSession(page, ADMIN_URL, memberToken(), 'tenant/agents')

    // 상단 바: 조직 이름·내 이름·내 계정 링크
    await expect(page.getByText('E2E 고객사').first()).toBeVisible()
    await expect(page.getByText('E2E 관리자').first()).toBeVisible()
    await expect(page.getByRole('link', { name: '내 계정' })).toHaveAttribute('href', 'https://auth.sandcorp.co.kr/account')

    // 초대 링크 만들기 → 링크가 한 번 보이고 대기 목록에 오른다
    await page.getByLabel('역할').selectOption('member')
    await page.getByRole('button', { name: '초대 링크 만들기' }).click()
    const url = await page.getByLabel('초대 링크').inputValue()
    expect(url).toContain('/api/auth/authorize/member?invite=')
    await expect(page.getByRole('button', { name: '초대 취소' })).toHaveCount(1)

    // 초대를 연 사람이 sandcorp 로그인을 마치면 콜백이 수락한다 — 대역에 원문을 넘긴다.
    dropInvite(new URL(url).searchParams.get('invite'))
    const api = await adminApi()
    await waitAccepted(api)
    await api.dispose()
    // 1회용 초대가 쓰여 새로고침하면 대기 목록에서 사라지고 목록에 오른다
    await page.reload()
    await expect(page.getByRole('row', { name: /E2E 초대받은 사람/ })).toHaveCount(1)
    await expect(page.getByRole('button', { name: '초대 취소' })).toHaveCount(0)

    const row = page.getByRole('row', { name: /E2E 초대받은 사람/ })
    await expect(row).toContainText('e2e-invitee@example.com')
    await expect(row).toContainText('팀원')
    await expect(row).toContainText('활성')

    // 수락한 사람의 세션은 이 조직으로 열린다
    const inviteePage = await page.context().newPage()
    await openMemberSession(inviteePage, ADMIN_URL, inviteeToken(), 'tenant/agents')
    await expect(inviteePage.getByText('E2E 고객사').first()).toBeVisible()
    await expect(inviteePage.getByRole('row', { name: /E2E 관리자/ })).toBeVisible()
    await inviteePage.close()

  })

  // 역할·마지막 Admin 보호·내보내기는 초대 수락까지를 API로 준비해 따로 본다 — 한 시험에 다 담으면 3초 한도를 넘는다(#257).
  test('역할 변경은 새로고침해도 유지되고, 마지막 관리자는 내보낼 수 없고, 내보낸 사람은 「내보냄」으로 남는다', async ({ page }) => {
    const api = await adminApi()
    const created = await (await api.post('/api/tenant/invites/', { data: { role: 'member' } })).json()
    dropInvite(created.token)
    const invitee = await waitAccepted(api)
    // 이미 있는 멤버를 다시 초대하면 역할을 지키므로, 앞 실행이 남긴 관리자는 팀원으로 되돌려 시작한다.
    if (invitee.role !== 'member') expect((await api.patch(`/api/tenant/members/${invitee.id}/role`, { data: { role: 'member' } })).ok()).toBeTruthy()
    await api.dispose()

    await openMemberSession(page, ADMIN_URL, memberToken(), 'tenant/agents')
    const row = page.getByRole('row', { name: /E2E 초대받은 사람/ })
    await row.getByRole('button', { name: '관리자로' }).click()
    await expect(row.getByRole('button', { name: '팀원으로' })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('row', { name: /E2E 초대받은 사람/ }).getByRole('button', { name: '팀원으로' })).toBeVisible()

    // 마지막 Admin 보호: 관리자 둘일 때 초대받은 사람을 팀원으로 되돌리고, 시드 Admin 혼자 남으면 내보낼 수 없다
    await page.getByRole('row', { name: /E2E 초대받은 사람/ }).getByRole('button', { name: '팀원으로' }).click()
    page.once('dialog', (d) => d.accept())
    await page.getByRole('row', { name: /E2E 관리자/ }).getByRole('button', { name: '내보내기' }).click()
    await expect(page.getByRole('alert')).toContainText('마지막 관리자는 내보낼 수 없습니다')

    // 초대받은 사람을 내보낸다 → 새로고침해도 「내보냄」
    page.once('dialog', (d) => d.accept())
    await page.getByRole('row', { name: /E2E 초대받은 사람/ }).getByRole('button', { name: '내보내기' }).click()
    await page.reload()
    await expect(page.getByRole('row', { name: /E2E 초대받은 사람/ })).toContainText('내보냄')
  })
})
