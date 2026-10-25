// 편집기가 기대는 「바깥」을 한 자리에 모은다(#37). 로그인한 테넌트 편집기와 로그인 없는 체험(/try)이 같은
// FlowEditorPage·TestPanel 을 쓰되, 어느 경로·인증으로 부르는지와 어떤 UI 를 보이는지는 여기서만 갈린다.
// 편집기 코드에 「체험이면…」을 흩뿌리지 않고, 화면 쪽(TryFlowPage)이 Provider 로 체험 백엔드를 끼운다.
import { createContext, useContext } from 'react'
import { appsFlowsApiFlowsGet, appsFlowsApiFlowsGetVersion, appsFlowsApiFlowsNodeSchema, appsFlowsApiFlowsPatchDraft } from '../generated/endpoints/flows/flows'
import { appsTenantsApiGetConfig } from '../generated/endpoints/tenant/tenant'
import { appsTenantsApiMemberMe } from '../generated/endpoints/tenant-members/tenant-members'
import type { PublicFlowOut } from '../generated/model'
import { appsTenantsTryApiTryFlowGet, appsTenantsTryApiTryNodeSchema, appsTenantsTryApiTryPatchDraft, appsTenantsTryApiTryTemplateDetail } from '../generated/endpoints/try/try'
import type { DraftPatchIn, FlowDetailOut } from '../generated/model'
import type { FlowError, FlowGraph } from './flowGraph'
import type { SseEvent } from './testTrace'
import { streamTestTurn } from './testStream'
import { useIsAdmin } from './useAgentRole'

export type NodeSchemas = Record<string, { config: object }>

export interface FlowEditorBackend {
  /** 흐름 하나(이름·초안·버전·배포 상태). */
  loadFlow(id: string): Promise<FlowDetailOut>
  /** 저장된 버전 보기 — 없으면(체험) 버전 UI 가 없다. */
  loadVersion?(id: string, n: number): Promise<{ graph: Partial<FlowGraph> }>
  /** 패치 연산을 초안에 저장하고 검증 오류를 돌려준다. */
  patchDraft(id: string, ops: unknown): Promise<{ errors: FlowError[] }>
  nodeSchemas(): Promise<NodeSchemas>
  streamTest(flowId: string, message: string, sessionId: string | null, onEvent: (e: SseEvent) => void): Promise<void>
  /** 내 정보·승인 설정(배포 UI 용). 없으면 읽지 않는다. */
  loadAccount?(): Promise<{ me: { name: string; email: string } | null; requiresApproval: boolean }>
  /** 편집·시험 권한(훅 — 백엔드마다 모듈 상수라 호출 순서가 바뀌지 않는다). */
  useCanEdit(): boolean
  /** 버전 이력·배포 패널을 그리는가. false 면 `versionsNote` 한 줄로 대신한다. */
  versionsPanel: boolean
  versionsNote?: string
  /** 시험 대화 탭이 있는가. */
  testChat: boolean
  /** 새로고침 뒤 저장된 판단 기록(세션 API)을 복원하는가 — 체험엔 그 API 가 없다. */
  restoreTraces: boolean
  /** 못 돈 턴의 사유를 체험 한도(플랫폼 trial)에서 묻는가. */
  askTrial: boolean
  /** 왼쪽 위 「목록으로」 링크. */
  back: { to: string; label: string }
  /** 편집 권한이 없을 때 머리줄에 붙일 설명. */
  readOnlyNotice: string
  /** HTTP 도구 노드에서 테넌트 비밀·허용 도메인 목록을 읽는가 — 익명 공개 보기엔 테넌트 API 가 없다. */
  tenantLookups: boolean
}

const tenantBackend: FlowEditorBackend = {
  loadFlow: async (id) => (await appsFlowsApiFlowsGet(id)).data as unknown as FlowDetailOut,
  loadVersion: async (id, n) => (await appsFlowsApiFlowsGetVersion(id, n)).data as unknown as { graph: Partial<FlowGraph> },
  patchDraft: async (id, ops) => {
    const r = await appsFlowsApiFlowsPatchDraft(id, { ops } as unknown as DraftPatchIn)
    return { errors: r.data.errors as FlowError[] }
  },
  nodeSchemas: async () => ((await appsFlowsApiFlowsNodeSchema()).data as unknown as { kinds?: NodeSchemas }).kinds ?? {},
  streamTest: (flowId, message, sessionId, onEvent) => streamTestTurn(flowId, message, sessionId, onEvent),
  loadAccount: async () => {
    const [me, cfg] = await Promise.all([
      appsTenantsApiMemberMe().then((r) => { const m = r.data as unknown as { name?: string; email?: string }; return { name: m.name ?? '', email: m.email ?? '' } }).catch(() => null),
      appsTenantsApiGetConfig().then((r) => !!(r.data as unknown as { deploy_requires_approval?: boolean }).deploy_requires_approval).catch(() => false),
    ])
    return { me, requiresApproval: cfg }
  },
  useCanEdit: useIsAdmin, // flow.edit·deploy·approve 는 Admin 전용(백엔드 permissions.py 와 같은 판정)
  versionsPanel: true,
  testChat: true,
  restoreTraces: true,
  askTrial: true,
  back: { to: '/tenant/flows', label: '흐름 목록' },
  readOnlyNotice: '읽기 전용 — 흐름 편집 권한이 없습니다',
  tenantLookups: true,
}

/** 체험(/try): 쿠키 세션, 같은 모양의 응답. 버전·배포는 없고 판단 기록은 SSE trace 로만 그린다. */
export const tryBackend: FlowEditorBackend = {
  loadFlow: async (id) => (await appsTenantsTryApiTryFlowGet(id)).data as unknown as FlowDetailOut,
  patchDraft: async (id, ops) => {
    const r = await appsTenantsTryApiTryPatchDraft(id, { ops } as unknown as DraftPatchIn)
    return { errors: r.data.errors as FlowError[] }
  },
  nodeSchemas: async () => ((await appsTenantsTryApiTryNodeSchema()).data as unknown as { kinds?: NodeSchemas }).kinds ?? {},
  streamTest: (flowId, message, sessionId, onEvent) => streamTestTurn(flowId, message, sessionId, onEvent, undefined, '/api/try'),
  useCanEdit: () => true,
  versionsPanel: false,
  versionsNote: '체험에서는 저장된 버전·배포가 없습니다. 배포·버전 저장은 가입한 뒤 내 테넌트에서 할 수 있어요.',
  testChat: true,
  restoreTraces: false,
  askTrial: false,
  back: { to: '/try', label: '템플릿 고르기' },
  readOnlyNotice: '읽기 전용',
  tenantLookups: true,
}

/** 「흐름 보기」: 세션 없이 템플릿의 그래프만 읽는다. 편집·시험 대화 없음. */
export const tryViewBackend: FlowEditorBackend = {
  ...tryBackend,
  loadFlow: async (templateId) => {
    const t = (await appsTenantsTryApiTryTemplateDetail(templateId)).data
    const graph = t.graph as unknown as FlowGraph
    return {
      flow: { id: t.id, name: t.name, is_public: false, deployed_version: null, updated_at: '', has_pending_request: false },
      draft: { graph, errors: [], valid: true },
      versions: [],
      pending_request: null,
    } as unknown as FlowDetailOut
  },
  patchDraft: async () => ({ errors: [] }),
  useCanEdit: () => false,
  versionsNote: '템플릿 원본을 읽기만 합니다. 「고쳐 보기」로 내 사본을 만들면 고칠 수 있어요.',
  testChat: false,
  readOnlyNotice: '읽기 전용 — 템플릿 원본입니다',
}

/** 공개 흐름 보기(#39): 이미 읽은 공개 응답(허용 목록 투영)을 그대로 보인다. 편집·시험 대화·판단 기록·버전 없음. */
export function publicViewBackend(pub: PublicFlowOut): FlowEditorBackend {
  const graph = pub.graph as unknown as FlowGraph
  return {
    ...tryViewBackend,
    loadFlow: async () => ({
      flow: { id: pub.slug, name: pub.flow_name, is_public: true, deployed_version: pub.version, updated_at: '', has_pending_request: false },
      draft: { graph, errors: [], valid: true },
      versions: [],
      pending_request: null,
    } as unknown as FlowDetailOut),
    versionsNote: `${pub.brand_name ? `${pub.brand_name} · ` : ''}배포 중인 v${pub.version}을 읽기만 합니다. 비밀·내부 주소·헤더·본문 값은 공개되지 않아요.`,
    readOnlyNotice: '읽기 전용 — 비밀·내부 주소·헤더·본문은 숨김',
    back: { to: '/try', label: '체험 템플릿' },
    tenantLookups: false,
  }
}

const Ctx = createContext<FlowEditorBackend>(tenantBackend)
export const FlowEditorBackendProvider = Ctx.Provider
export const useFlowEditorBackend = () => useContext(Ctx)
