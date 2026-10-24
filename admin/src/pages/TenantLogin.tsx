import { useEffect, useState } from 'react'
import { memberLoginUrl, memberTenants, selectTenant } from '../api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import TenantOnboarding from './TenantOnboarding'

// 고객(팀원) 로그인(#56). 비밀번호 폼 없이 sandcorp-auth로 보낸다. 콜백이 돌려보낸 쿼리로 다음 걸음을 정한다:
// ?select_tenant=1 → 조직 선택, ?onboarding=1 → 조직 없음, ?login_error=<사유> → 사유별 문구.
const LOGIN_ERRORS: Record<string, string> = {
  invite_expired: '초대 링크가 만료되었습니다. 관리자에게 새 링크를 받으세요.',
  invite_used: '이미 사용된 초대 링크입니다. 관리자에게 새 링크를 받으세요.',
  invite_invalid: '유효하지 않은 초대 링크입니다. 링크가 끝까지 복사되었는지 확인하세요.',
  owner: '대표 계정은 고객 화면에 들어올 수 없습니다. 관리 화면(Operator)에서 로그인하세요.',
  denied: 'sandcorp 계정 로그인이나 동의를 취소했습니다.',
  state: '로그인 시도가 만료되었거나 다른 창에서 시작되었습니다. 다시 로그인하세요.',
  exchange: 'sandcorp 계정 응답을 확인하지 못했습니다. 다시 로그인하세요.',
  token: 'sandcorp 계정 응답을 확인하지 못했습니다. 다시 로그인하세요.',
  config: 'Flow가 아직 sandcorp 계정 로그인으로 설정되지 않았습니다. 관리자에게 알려 주세요.',
}
const UNKNOWN_ERROR = '로그인하지 못했습니다. 다시 시도하세요.'

const ROLE_LABEL: Record<string, string> = { admin: '관리자', member: '팀원' }

type Tenant = { tenant_id: string; tenant_name: string; role: string }

export default function TenantLogin({ onLogin }: { onLogin: (name: string) => void }) {
  const params = new URLSearchParams(window.location.search)
  const loginError = params.get('login_error')
  const selecting = params.get('select_tenant') === '1'
  const onboarding = params.get('onboarding') === '1'
  const [tenants, setTenants] = useState<Tenant[] | null>(null)
  const [who, setWho] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!selecting) return
    memberTenants()
      .then((d) => { setTenants(d.tenants); setWho(d.name || d.email || '') })
      .catch(() => setError('로그인이 만료되었습니다. 다시 로그인하세요.'))
  }, [selecting])

  const choose = async (t: Tenant) => {
    setError('')
    try {
      await selectTenant(t.tenant_id)
      onLogin(who)
    } catch {
      setError('이 조직에 들어갈 수 없습니다.')
    }
  }

  const showTenants = selecting && tenants !== null
  const title = showTenants ? '조직 선택' : onboarding ? '시작하기' : loginError ? '로그인할 수 없습니다' : '로그인'

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4 text-foreground">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">{title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 break-keep">
          {loginError && (
            <p role="alert" className="text-sm leading-relaxed text-red-600 dark:text-red-400">
              {LOGIN_ERRORS[loginError] ?? UNKNOWN_ERROR}
            </p>
          )}
          {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}

          {showTenants ? (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">
                {who ? `${who}님, ` : ''}들어갈 조직을 고르세요.
              </p>
              {tenants.map((t) => (
                <Button key={t.tenant_id} type="button" variant="outline" className="h-auto w-full justify-between py-3" onClick={() => choose(t)}>
                  <span className="truncate">{t.tenant_name}</span>
                  <span className="ml-3 shrink-0 text-xs text-muted-foreground">{ROLE_LABEL[t.role] ?? t.role}</span>
                </Button>
              ))}
            </div>
          ) : onboarding ? (
            <TenantOnboarding />
          ) : (
            <>
              {!loginError && (
                <p className="text-sm leading-relaxed text-muted-foreground">
                  sandcorp 계정으로 로그인합니다. 팀 초대 링크를 받았다면 그 링크를 열어 로그인하세요.
                </p>
              )}
              <Button asChild className="w-full">
                <a href={memberLoginUrl()}>{loginError ? '다시 로그인' : 'sandcorp 계정으로 로그인'}</a>
              </Button>
            </>
          )}

          <p className="text-xs text-muted-foreground">
            관리 화면 운영자라면 <a className="underline underline-offset-4" href="/admin-ui/operator">여기</a>
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
