import { useEffect, useState } from 'react'
import { memberLoginUrl, memberTenants, selectTenant } from '../api'
import { s } from '../styles'

// 고객 로그인(#40) — sandcorp-auth로 보낸다. 콜백이 돌려보낸 쿼리로 다음 걸음을 정한다.
// 최소 화면이다. 온보딩(테넌트 만들기)은 #41, 화면 개편은 #56.
const LOGIN_ERRORS: Record<string, string> = {
  invite_expired: '초대 링크가 만료되었습니다. 관리자에게 새 링크를 받으세요.',
  invite_used: '이미 사용된 초대 링크입니다.',
  invite_invalid: '유효하지 않은 초대 링크입니다.',
  owner: '대표 계정은 Operator 화면(/admin-ui/operator)으로 로그인하세요.',
}

type Tenant = { tenant_id: string; tenant_name: string; role: string }

export default function TenantLogin({ onLogin }: { onLogin: (name: string) => void }) {
  const params = new URLSearchParams(window.location.search)
  const loginError = params.get('login_error')
  const selecting = params.get('select_tenant') === '1'
  const onboarding = params.get('onboarding') === '1'
  const [tenants, setTenants] = useState<Tenant[] | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!selecting) return
    memberTenants()
      .then((d) => { setTenants(d.tenants); setName(d.name || d.email || '') })
      .catch(() => setError('로그인이 만료되었습니다. 다시 로그인하세요.'))
  }, [selecting])

  const choose = async (t: Tenant) => {
    try {
      await selectTenant(t.tenant_id)
      onLogin(name)
    } catch {
      setError('이 조직에 들어갈 수 없습니다.')
    }
  }

  return (
    <div style={s.center}>
      <div style={s.card}>
        <h2 style={s.title}>{selecting ? '조직 선택' : 'Tenant 로그인'}</h2>
        {loginError && <p style={s.error}>{LOGIN_ERRORS[loginError] || '로그인에 실패했습니다.'}</p>}
        {onboarding && <p style={{ fontSize: 14 }}>아직 속한 조직이 없습니다. 관리자에게 초대 링크를 받으세요.</p>}
        {error && <p style={s.error}>{error}</p>}
        {selecting && tenants ? (
          <div style={s.form}>
            {tenants.map((t) => (
              <button key={t.tenant_id} style={s.btn} type="button" onClick={() => choose(t)}>
                {t.tenant_name} ({t.role === 'admin' ? 'Admin' : 'Member'})
              </button>
            ))}
          </div>
        ) : (
          <a style={{ ...s.btn, display: 'block', textAlign: 'center', textDecoration: 'none' }} href={memberLoginUrl()}>
            sandcorp 계정으로 로그인
          </a>
        )}
        <p style={{ marginTop: 16, fontSize: 13, color: '#718096' }}>
          {'Operator는 '}
          <a href="/admin-ui/operator">여기</a>
        </p>
      </div>
    </div>
  )
}
