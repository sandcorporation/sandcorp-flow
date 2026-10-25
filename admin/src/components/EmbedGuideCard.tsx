import { useState } from 'react'
import { Button } from '@/components/ui/button'

// 운영에서 쓰는 방식(docs/deployment.md portfolio 표)과 같은 iframe 문자열. 비밀은 없다.
export function buildIframeCode(origin: string, slug: string): string {
  return `<iframe src="${origin}/chatbot/${slug}/" width="400" height="600" style="border:none" allow="microphone"></iframe>`
}

const hint = 'mt-1 text-xs text-muted-foreground'

type Props = { origin: string; slug: string; isAdmin: boolean }

// 「내 사이트에 붙이기」 — slug 기반 위젯 embed 코드 복사·미리보기. 읽기 전용 안내라 Member도 볼 수 있다.
export default function EmbedGuideCard({ origin, slug, isAdmin }: Props) {
  const [copied, setCopied] = useState<'' | 'url' | 'iframe'>('')
  const publicUrl = slug ? `${origin}/chatbot/${slug}/` : ''
  const code = slug ? buildIframeCode(origin, slug) : ''

  const copy = async (text: string, which: 'url' | 'iframe') => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(which)
      setTimeout(() => setCopied(''), 2000)
    } catch { /* clipboard 거부 시 무시 */ }
  }

  return (
    <section aria-label="내 사이트에 붙이기" className="min-w-0 space-y-3 rounded-lg border border-border bg-card p-4">
      <div>
        <h3 className="text-sm font-semibold">내 사이트에 붙이기</h3>
        <p className={hint}>아래 코드를 내 사이트 HTML의 원하는 자리에 붙여 넣으면 공개 챗봇이 뜹니다.</p>
      </div>
      {slug ? (
        <>
          <pre data-testid="embed-code" tabIndex={0}
            className="max-w-full overflow-x-auto rounded bg-muted p-3 text-xs leading-relaxed">{code}</pre>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" type="button" onClick={() => copy(code, 'iframe')}>
              {copied === 'iframe' ? '✓ 복사됨' : '임베드 코드 복사'}
            </Button>
            <Button size="sm" variant="outline" type="button" onClick={() => copy(publicUrl, 'url')}>
              {copied === 'url' ? '✓ 복사됨' : 'URL 복사'}
            </Button>
            <a href={publicUrl} target="_blank" rel="noopener noreferrer"
              className="text-sm text-primary underline-offset-4 hover:underline">미리보기 열기 ↗</a>
          </div>
          <p className={hint}>입력창의 slug 기준입니다. 바꿨다면 먼저 Slug를 저장하세요. 저장 뒤 slug가 바뀌면 이미 붙여 둔 코드가 끊깁니다.</p>
          <p className={hint}>이 코드에는 비밀 키가 들어 있지 않습니다. 방문자 신원검증(HMAC)을 쓴다면 해시는 내 서버에서 만들어 주세요 — 비밀 키를 브라우저 코드에 넣지 마세요.</p>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          {isAdmin
            ? '공개 챗봇 주소(slug)가 아직 없어요. 위에서 slug를 먼저 정하고 저장하면 여기에 붙일 코드가 나타납니다.'
            : '공개 챗봇 주소(slug)가 아직 없어요. 관리자가 slug를 정하면 여기에 붙일 코드가 나타납니다.'}
        </p>
      )}
    </section>
  )
}
