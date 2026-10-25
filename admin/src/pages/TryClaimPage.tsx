import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ClaimError, claimLoginUrl, claimTryFlow, memberTenants, selectTenant, trySessionTemplate } from '../api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import FlowMark from '../components/FlowMark'
import TenantOnboarding from './TenantOnboarding'

type Tenant = { tenant_id: string; tenant_name: string; role: string }
type Loaded =
  | { state: 'loading' }
  | { state: 'login' } // 표(member_ticket)가 없다 — 로그인 전이거나 10분이 지났다
  | { state: 'no_session' } // 옮길 체험이 없다(만료·이미 옮김)
  | { state: 'ready'; who: string; admins: Tenant[] }

/** /try/claim — 「이 흐름으로 가입」(#42) 로그인에서 돌아온 곳. 새 테넌트를 만들거나(기존 온보딩 그대로) Admin인
 *  테넌트를 골라(기존 선택 그대로) 그 세션으로 체험 사본을 옮기고, 그 테넌트 편집기의 새 흐름으로 간다. */
export default function TryClaimPage({ onLogin }: { onLogin: (who: string) => void }) {
  const nav = useNavigate()
  const [loaded, setLoaded] = useState<Loaded>({ state: 'loading' })
  const [mode, setMode] = useState<'new' | 'existing'>('new')
  const [picked, setPicked] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [orphan, setOrphan] = useState('') // 새 테넌트는 만들었는데 옮기기가 거절됐다

  useEffect(() => {
    Promise.all([memberTenants().catch(() => null), trySessionTemplate()]).then(([me, template]) => {
      if (!me) return setLoaded({ state: 'login' })
      if (!template) return setLoaded({ state: 'no_session' })
      const admins = me.tenants.filter((t) => t.role === 'admin')
      setPicked(admins[0]?.tenant_id ?? '')
      setLoaded({ state: 'ready', who: me.name || me.email || '', admins })
    })
  }, [])

  const moveNow = async (who: string) => {
    const claimed = await claimTryFlow()
    onLogin(who)
    nav(`/tenant/flows/${claimed.flow_id}?claimed=1`, { replace: true })
  }

  const afterCreate = async (who: string) => {
    try { await moveNow(who) } catch (e) { setOrphan(who); setError(e instanceof ClaimError ? e.message : String(e)) }
  }

  const addToExisting = async (who: string) => {
    setBusy(true); setError('')
    try {
      await selectTenant(picked)
      await moveNow(who)
    } catch (e) {
      setError(e instanceof Error ? e.message : '흐름을 옮기지 못했습니다. 다시 시도하세요.')
    } finally { setBusy(false) }
  }

  const body = () => {
    if (loaded.state === 'loading') return <p aria-busy="true" className="text-sm text-muted-foreground">불러오는 중…</p>
    if (loaded.state === 'login') {
      return (
        <div className="space-y-3 text-sm leading-relaxed">
          <p className="text-muted-foreground">sandcorp 계정으로 로그인하면 체험한 흐름을 내 테넌트로 옮겨요.</p>
          <Button asChild className="w-full"><a href={claimLoginUrl()}>sandcorp 계정으로 로그인</a></Button>
        </div>
      )
    }
    if (loaded.state === 'no_session') {
      return (
        <div className="space-y-3 text-sm leading-relaxed">
          <p role="alert">옮길 체험이 없어요. 체험이 만료됐거나 이미 옮겼을 수 있어요.</p>
          <Button asChild variant="outline" className="w-full"><Link to="/try">템플릿 고르기</Link></Button>
        </div>
      )
    }
    const { who, admins } = loaded
    if (orphan) {
      return (
        <div className="space-y-3 text-sm leading-relaxed">
          <p role="alert" className="text-destructive">{error}</p>
          <p className="text-muted-foreground">테넌트는 만들어졌어요. 흐름은 비어 있는 기본 흐름부터 시작할 수 있어요.</p>
          <Button className="w-full" onClick={() => { onLogin(orphan); nav('/tenant/flows', { replace: true }) }}>내 테넌트로 가기</Button>
        </div>
      )
    }
    return (
      <div className="space-y-4 text-sm leading-relaxed">
        <p className="text-muted-foreground">
          {who ? `${who}님, ` : ''}체험한 흐름을 <b className="text-ink">새 흐름(초안)</b>으로 옮겨요. 배포하기 전까지 손님에게는 보이지 않아요.
        </p>
        {admins.length > 0 && (
          <fieldset className="space-y-2">
            <legend className="sr-only">옮길 곳</legend>
            {([['new', '새 테넌트 만들기'], ['existing', '기존 테넌트에 추가']] as const).map(([value, label]) => (
              <label key={value} className="flex cursor-pointer items-center gap-2 border border-line-soft px-3 py-2">
                <input type="radio" name="claim-mode" value={value} checked={mode === value} onChange={() => { setMode(value); setError('') }} />
                {label}
              </label>
            ))}
          </fieldset>
        )}
        {mode === 'existing' && admins.length > 0 ? (
          <div className="space-y-3">
            <fieldset className="space-y-1">
              <legend className="mb-1 text-xs text-muted-foreground">관리자로 있는 테넌트</legend>
              {admins.map((t) => (
                <label key={t.tenant_id} className="flex min-w-0 cursor-pointer items-center gap-2 px-1 py-1">
                  <input type="radio" name="claim-tenant" value={t.tenant_id} checked={picked === t.tenant_id} onChange={() => setPicked(t.tenant_id)} />
                  <span className="truncate">{t.tenant_name}</span>
                </label>
              ))}
            </fieldset>
            {error && <p role="alert" className="text-destructive">{error}</p>}
            <Button className="w-full" disabled={busy || !picked} onClick={() => addToExisting(who)}>
              {busy ? '옮기는 중…' : '이 테넌트로 옮기기'}
            </Button>
          </div>
        ) : (
          <TenantOnboarding
            onCreated={afterCreate}
            intro={() => null}
            submitLabel="만들고 흐름 옮기기"
            loginHref={claimLoginUrl()}
          />
        )}
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4 text-foreground">
      <Card className="w-full max-w-sm min-w-0">
        <CardHeader>
          <FlowMark className="mb-2 h-10 w-10" />
          <CardTitle className="text-xl">이 흐름으로 가입</CardTitle>
        </CardHeader>
        <CardContent className="break-keep">{body()}</CardContent>
      </Card>
    </div>
  )
}
