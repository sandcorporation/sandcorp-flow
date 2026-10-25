import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { appsFlowsPublicApiGetPublicFlow } from '../generated/endpoints/public/public'
import type { PublicFlowOut } from '../generated/model'
import { FlowEditorBackendProvider, publicViewBackend } from '@/lib/flowEditorBackend'
import { shortTemplate } from '@/lib/tryTemplates'
import FlowEditorPage from '../components/FlowEditorPage'
import TryShell from '../components/TryShell'
import { Button } from '../components/ui/button'

type State = { kind: 'loading' } | { kind: 'missing' } | { kind: 'failed' } | { kind: 'ready'; pub: PublicFlowOut }

/** /flows/:slug — 공개 흐름 보기(#39). 로그인 없이 테넌트가 공개한 흐름의 배포 버전을 읽기 전용 편집기로 보인다. */
export default function PublicFlowPage() {
  const { id: slug = '' } = useParams()
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    let live = true
    setState({ kind: 'loading' })
    appsFlowsPublicApiGetPublicFlow(slug)
      .then((r) => live && setState({ kind: 'ready', pub: r.data as PublicFlowOut }))
      .catch((e: { status?: number }) => live && setState({ kind: e?.status === 404 ? 'missing' : 'failed' }))
    return () => { live = false }
  }, [slug])

  const backend = useMemo(() => (state.kind === 'ready' ? publicViewBackend(state.pub) : null), [state])
  const template = state.kind === 'ready' && state.pub.template ? shortTemplate(state.pub.template) : null
  const right = template ? (
    <Button asChild variant="head" size="sm"><Link to={`/try?template=${template}`}>고쳐 보기</Link></Button>
  ) : null

  return (
    <TryShell right={right} fill title="Sandcorp Flow">
      {state.kind === 'loading' && <p className="text-sm text-muted-foreground" role="status">흐름을 불러오는 중…</p>}
      {state.kind === 'missing' && <p className="text-sm text-muted-foreground" role="alert">공개된 흐름이 없습니다.</p>}
      {state.kind === 'failed' && <p className="text-sm text-destructive" role="alert">흐름을 불러오지 못했습니다. 잠시 뒤 다시 열어 주세요.</p>}
      {backend && (
        <FlowEditorBackendProvider value={backend}>
          <FlowEditorPage />
        </FlowEditorBackendProvider>
      )}
    </TryShell>
  )
}
