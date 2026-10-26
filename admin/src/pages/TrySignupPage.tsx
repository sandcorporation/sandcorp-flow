import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useSearchParams } from 'react-router-dom'
import { appsTenantsTryApiTrySessionGet, appsTenantsTryApiTryStart } from '../generated/endpoints/try/try'
import type { TryStartInGraph } from '../generated/model'
import { claimLoginUrl } from '../api'
import { readDraft } from '@/lib/flowEditorBackend'
import { shortTemplate, templateId, tryFailureText } from '@/lib/tryTemplates'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'

/** 검색 색인에서 뺀다(ADR-0032 Q4): 크롤러가 이 주소를 따라와도 세션을 만들지 못하게 robots 메타를 이 페이지에만 둔다. */
function useNoIndex() {
  useEffect(() => {
    const meta = document.createElement('meta')
    meta.name = 'robots'
    meta.content = 'noindex,nofollow'
    document.head.appendChild(meta)
    return () => { meta.remove() }
  }, [])
}

/**
 * 「이 흐름으로 가입」 중간 페이지(#198): 세션이 없으면 만든 뒤, 있으면 그대로 옮기기 로그인으로 `location.replace` 한다.
 * 임베드 iframe 은 새 탭을 동기적으로 열어야 해서(팝업 차단) 링크가 이 페이지를 가리키고, 세션 만들기는 여기서 한다.
 * 새 탭의 sessionStorage 는 iframe 과 다르므로 보관된 지역 그래프는 같은 탭에서 온 경우에만 실린다 — 없으면 템플릿 그대로.
 */
export default function TrySignupPage() {
  useNoIndex()
  const [params] = useSearchParams()
  const short = params.get('template')
  const template = templateId(short)
  const [error, setError] = useState('')
  const started = useRef(false)

  useEffect(() => {
    if (!template || started.current) return
    started.current = true
    ;(async () => {
      try {
        const existing = (await appsTenantsTryApiTrySessionGet().catch(() => null))?.data
        if (!existing) {
          const graph = readDraft(template) as unknown as TryStartInGraph
          await appsTenantsTryApiTryStart({ template, graph })
        }
        window.location.replace(claimLoginUrl())
      } catch (e) {
        setError(tryFailureText(e))
      }
    })()
  }, [template])

  if (!template) return <Navigate to="/try" replace />
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4 text-foreground">
      <Card className="w-full max-w-sm min-w-0">
        <CardHeader><CardTitle className="text-xl">이 흐름으로 가입</CardTitle></CardHeader>
        <CardContent className="break-keep space-y-3">
          {error ? (
            <>
              <p role="alert" className="text-sm">{error}</p>
              <Button asChild variant="outline"><Link to={`/try?template=${encodeURIComponent(shortTemplate(template))}`}>체험으로 돌아가기</Link></Button>
            </>
          ) : (
            <p role="status" aria-busy="true" className="text-sm text-muted-foreground">가입 화면으로 옮기는 중입니다…</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
