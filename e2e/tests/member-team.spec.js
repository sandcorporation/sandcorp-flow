const { test, expect } = require('@playwright/test')
const { memberToken, inviteeToken, dropInvite, openMemberSession } = require('./memberToken')
const { trackRequests } = require('./settle')

const ADMIN_URL = process.env.ADMIN_URL || 'http://localhost:5174'

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
    const net = trackRequests(page)
    await expect.poll(async () => {
      // 다시 읽은 멤버·초대 목록이 도착한 뒤에 센다 — 바로 세면 아직 그려지지 않은 화면을 세고 곧 다시 새로고침해 영영 못 본다
      const listed = Promise.all(['members', 'invites'].map((name) => page.waitForResponse((r) => new URL(r.url()).pathname === `/api/tenant/${name}/` && r.request().method() === 'GET')))
      await page.reload()
      await listed
      await net.idle()
      const pending = await page.getByRole('button', { name: '초대 취소' }).count()
      const rows = await page.getByRole('row', { name: /E2E 초대받은 사람/ }).count()
      return `${pending}|${rows}`
    }, { timeout: 20_000 }).toBe('0|1')  // 1회용 초대가 쓰여 대기 목록에서 사라지고 목록에 올랐다

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

    // 역할 변경 → 새로고침해도 유지. (이미 있는 멤버를 다시 초대하면 역할을 지키므로, 앞 실행이 남긴 관리자는 팀원으로 되돌려 시작한다.)
    if (await row.getByRole('button', { name: '팀원으로' }).count()) {
      await row.getByRole('button', { name: '팀원으로' }).click()
    }
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
