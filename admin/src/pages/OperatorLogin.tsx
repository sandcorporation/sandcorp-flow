import { useSearchParams } from 'react-router-dom'
import { startOperatorLogin } from '../api'
import { s } from '../styles'

// 백엔드 콜백(/api/auth/callback)이 거절하면 ?login_error=<사유>로 돌려보낸다(#21).
const REJECTION: Record<string, string> = {
  member: 'Operator 화면은 대표 계정만 들어올 수 있습니다. 일반 사용자 로그인은 아직 열리지 않았습니다.',
  admin: '이 계정에는 Flow 관리(admin) 권한이 없습니다. sandcorp-auth에서 Flow 리소스의 admin 스코프를 확인하세요.',
  denied: 'sandcorp-auth에서 로그인이나 동의를 취소했습니다.',
  state: '로그인 시도가 만료되었거나 다른 창에서 시작되었습니다. 다시 로그인하세요.',
  exchange: 'sandcorp-auth 응답을 확인하지 못했습니다. 다시 로그인하세요.',
  token: 'sandcorp-auth 응답을 확인하지 못했습니다. 다시 로그인하세요.',
  config: 'Flow가 아직 sandcorp-auth 클라이언트로 설정되지 않았습니다(AUTH_CLIENT_ID·AUTH_CLIENT_SECRET).',
}
const UNKNOWN = '로그인하지 못했습니다. 다시 시도하세요.'

export default function OperatorLogin() {
  const [params] = useSearchParams()
  const reason = params.get('login_error')
  const muted = { marginTop: 16, fontSize: 13, color: '#718096' }

  return (
    <div style={s.center}>
      {/* 카드가 흰 배경 고정이라 다크 테마에서도 글자색을 카드 쪽에 묶는다(HMI 테마 #27 전까지). */}
      <div style={{ ...s.card, color: '#1a202c', wordBreak: 'keep-all' }}>
        {reason ? (
          <>
            <h2 style={s.title}>로그인할 수 없습니다</h2>
            <p role="alert" style={{ ...s.error, fontSize: 14, lineHeight: 1.6, marginBottom: 20 }}>
              {REJECTION[reason] ?? UNKNOWN}
            </p>
          </>
        ) : (
          <>
            <h2 style={s.title}>Operator 로그인</h2>
            <p style={{ fontSize: 14, color: '#4a5568', lineHeight: 1.6, marginBottom: 20 }}>
              sandcorp 대표 계정으로 로그인합니다.
            </p>
          </>
        )}
        <button style={{ ...s.btn, width: '100%' }} type="button" onClick={() => startOperatorLogin()}>
          {reason ? '다시 로그인' : 'sandcorp 계정으로 로그인'}
        </button>
        <p style={muted}>
          Tenant로 로그인하려면 <a href="/admin-ui/tenant">여기</a>
        </p>
      </div>
    </div>
  )
}
