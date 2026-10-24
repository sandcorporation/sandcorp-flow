import { ACCOUNT_URL } from '../api'

// 속한 조직이 없는 사람(?onboarding=1)이 보는 자리. 조직 만들기는 #41이 이 컴포넌트를 채운다.
// 콜백이 심은 member_ticket 쿠키(경로 /api/auth/member)로 사용자를 알 수 있다.
export default function TenantOnboarding() {
  return (
    <div className="space-y-3 text-sm leading-relaxed" data-testid="tenant-onboarding">
      <p className="font-medium">아직 속한 조직이 없습니다.</p>
      <p className="rounded-md border border-dashed border-border bg-muted/40 p-3 text-muted-foreground">
        조직 만들기는 준비 중입니다. 먼저 쓰고 있는 팀이 있다면 관리자에게 초대 링크를 받아 합류하세요.
      </p>
      <a className="text-primary underline underline-offset-4" href={ACCOUNT_URL} target="_blank" rel="noreferrer">
        내 계정
      </a>
    </div>
  )
}
