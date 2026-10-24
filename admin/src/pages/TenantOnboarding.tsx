import { FormEvent, useEffect, useState } from 'react'
import { ACCOUNT_URL, createOrganization, memberLoginUrl, memberTenants } from '../api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

// 속한 조직이 없는 사람(?onboarding=1)이 조직을 만든다(#41). 콜백이 심은 member_ticket 쿠키(경로 /api/auth/member)로
// 서버가 사용자를 알고, 만들면 그 사람이 첫 관리자가 되어 바로 조직 화면으로 들어간다.

// 이름에서 공개 주소 후보를 만든다 — 서버 규칙(완성형 한글·영문·숫자·단일 하이픈)에 맞게 접는다.
export function suggestSlug(name: string): string {
  return name
    .normalize('NFC')
    .trim()
    .replace(/\s+/g, '-')
    .replace(/[^가-힣A-Za-z0-9-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 63)
}

export default function TenantOnboarding({ onCreated }: { onCreated: (who: string) => void }) {
  const [who, setWho] = useState('')
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [error, setError] = useState('')
  const [expired, setExpired] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    memberTenants()
      .then((d) => setWho(d.name || d.email || ''))
      .catch(() => setExpired(true))
  }, [])

  const onName = (v: string) => {
    setName(v)
    if (!slugTouched) setSlug(suggestSlug(v))
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await createOrganization(name.trim(), slug.trim())
      onCreated(who)
    } catch (err) {
      // OnboardingError(reason='expired')는 표가 만료된 경우다.
      if ((err as { reason?: string }).reason === 'expired') setExpired(true)
      else setError(err instanceof Error ? err.message : '조직을 만들지 못했습니다. 다시 시도하세요.')
    } finally {
      setBusy(false)
    }
  }

  if (expired) {
    return (
      <div className="space-y-3 text-sm leading-relaxed" data-testid="tenant-onboarding">
        <p role="alert" className="text-destructive">로그인이 만료되었습니다. 다시 로그인하세요.</p>
        <Button asChild className="w-full"><a href={memberLoginUrl()}>sandcorp 계정으로 로그인</a></Button>
      </div>
    )
  }

  return (
    <form className="space-y-4 text-sm leading-relaxed" data-testid="tenant-onboarding" onSubmit={submit}>
      <p className="text-muted-foreground">
        {who ? `${who}님, ` : ''}아직 속한 조직이 없습니다. 조직을 만들면 관리자로 바로 시작합니다.
        이미 쓰고 있는 팀이 있다면 관리자에게 초대 링크를 받아 합류하세요.
      </p>
      <div className="space-y-2">
        <Label htmlFor="org-name">조직 이름</Label>
        <Input id="org-name" value={name} maxLength={255} required autoComplete="organization"
          onChange={(e) => onName(e.target.value)} placeholder="예: 모래상회" />
      </div>
      <div className="space-y-2">
        <Label htmlFor="org-slug">공개 주소</Label>
        <div className="flex min-w-0 items-center rounded-md border border-input bg-background focus-within:ring-1 focus-within:ring-ring">
          <span className="shrink-0 pl-3 text-xs text-muted-foreground">/chatbot/</span>
          <input id="org-slug" value={slug} maxLength={63} required
            className="min-w-0 flex-1 bg-transparent px-1 py-2 text-sm outline-none"
            onChange={(e) => { setSlugTouched(true); setSlug(e.target.value) }} placeholder="morae" />
        </div>
        <p className="text-xs text-muted-foreground">손님이 챗봇을 여는 주소입니다. 한글·영문·숫자와 하이픈(-)을 쓸 수 있어요.</p>
      </div>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      <Button type="submit" className="w-full" disabled={busy || !name.trim() || !slug.trim()}>
        {busy ? '만드는 중…' : '조직 만들기'}
      </Button>
      <a className="inline-block text-foreground underline decoration-primary decoration-2 underline-offset-4" href={ACCOUNT_URL} target="_blank" rel="noreferrer">
        내 계정
      </a>
    </form>
  )
}
