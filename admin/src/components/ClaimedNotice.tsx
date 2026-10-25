import { Link } from 'react-router-dom'
import type { FlowGraph } from '@/lib/flowGraph'
import { isDemoApiUrl } from '@/lib/httpToolWarn'

/** 「이 흐름으로 가입」(#42)으로 체험에서 옮겨 온 흐름(?claimed=1)에 한 번 보이는 안내 — 초안이고, 지식은 비어 있고,
 *  가짜 API는 그대로라는 것. 지식·가짜 API 줄은 그 노드가 있을 때만 보인다. */
export default function ClaimedNotice({ graph }: { graph: FlowGraph }) {
  const knowledge = graph.nodes.some((n) => n.kind === 'knowledge_search')
  const demoApi = graph.nodes.some((n) => n.kind === 'http_tool' && typeof n.config?.url === 'string' && isDemoApiUrl(n.config.url))
  return (
    <div role="status" data-testid="claimed-notice" className="space-y-1 border-l-4 border-run bg-run/10 px-3 py-2 text-sm leading-relaxed text-ink break-keep">
      <p><b>체험에서 옮긴 흐름이에요.</b> 아직 초안이라 손님에게는 보이지 않아요 — 시험 대화로 확인한 뒤 배포하세요.</p>
      {knowledge && (
        <p>
          지식 검색 노드는 이제 이 테넌트의 지식을 찾아요. 체험 예제 문서는 옮기지 않았으니 지식 문서를 올리세요.{' '}
          <Link to="/tenant/documents" className="underline decoration-primary decoration-2 underline-offset-4">지식 문서 올리기</Link>
        </p>
      )}
      {demoApi && <p>HTTP 도구가 예제용 <b>가짜 API</b>를 불러요. 배포 전 실제 주소로 바꾸세요.</p>}
    </div>
  )
}
