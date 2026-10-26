// 편집기가 기대는 「바깥」을 한 자리에 모은다(#37). 로그인한 테넌트 편집기와 로그인 없는 체험(/try)이 같은
// FlowEditorPage·TestPanel 을 쓰되, 어느 경로·인증으로 부르는지와 어떤 UI 를 보이는지는 여기서만 갈린다.
// 편집기 코드에 「체험이면…」을 흩뿌리지 않고, 화면 쪽(TryFlowPage)이 Provider 로 체험 백엔드를 끼운다.
import { createContext, useContext } from 'react'
import { appsFlowsApiFlowsGet, appsFlowsApiFlowsGetVersion, appsFlowsApiFlowsNodeSchema, appsFlowsApiFlowsPatchDraft } from '../generated/endpoints/flows/flows'
import { appsTenantsApiGetConfig } from '../generated/endpoints/tenant/tenant'
import { appsTenantsApiMemberMe } from '../generated/endpoints/tenant-members/tenant-members'
import type { PublicFlowOut } from '../generated/model'
import { appsTenantsTryApiTryFlowGet, appsTenantsTryApiTryNodeSchema, appsTenantsTryApiTryPatchDraft, appsTenantsTryApiTrySessionGet, appsTenantsTryApiTryStart, appsTenantsTryApiTryTemplateDetail } from '../generated/endpoints/try/try'
import type { DraftPatchIn, FlowDetailOut, TrySessionOut, TryTemplateDetailOut } from '../generated/model'
import { applyOps, type FlowError, type FlowGraph, type PatchOp } from './flowGraph'
import type { SseEvent } from './testTrace'
import { streamTestTurn } from './testStream'
import { useIsAdmin } from './useAgentRole'
import { HttpError } from '../mutator'

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
  /** 머리줄에 초안 저장·검증 상태를 보이는가 — 초안이 없는 보기(템플릿 원본·공개 배포 버전)는 끈다. */
  draftStatus: boolean
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
  draftStatus: true,
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
  tenantLookups: false, // 체험엔 테넌트 허용 도메인·비밀이 없다(조회하면 401) — 경고도 숨기고 가짜 API 안내만 남는다,
  draftStatus: true,
}

/** 템플릿 원본 → 편집기가 읽는 흐름 응답 모양(초안 = 템플릿 그래프, 버전 없음). */
function templateFlow(t: TryTemplateDetailOut, graph: FlowGraph): FlowDetailOut {
  return {
    flow: { id: t.id, name: t.name, is_public: false, deployed_version: null, updated_at: '', has_pending_request: false },
    draft: { graph, errors: [], valid: true },
    versions: [],
    pending_request: null,
  } as unknown as FlowDetailOut
}

/** 「흐름 보기」: 세션 없이 템플릿의 그래프만 읽는다. 편집·시험 대화 없음. */
export const tryViewBackend: FlowEditorBackend = {
  ...tryBackend,
  loadFlow: async (templateId) => {
    const t = (await appsTenantsTryApiTryTemplateDetail(templateId)).data
    return templateFlow(t, t.graph as unknown as FlowGraph)
  },
  patchDraft: async () => ({ errors: [] }),
  useCanEdit: () => false,
  versionsNote: '템플릿 원본을 읽기만 합니다. 「고쳐 보기」로 내 사본을 만들면 고칠 수 있어요.',
  testChat: false,
  readOnlyNotice: '읽기 전용 — 템플릿 원본입니다',
  draftStatus: false,
}

/** 위치만 바꾸는 연산 묶음인가 — 노드 끌기만으로는 체험을 시작하지 않는다(#194 Q2). */
const positionOnly = (ops: PatchOp[]): boolean =>
  ops.length > 0 && ops.every((o) => o.op === 'upsert_node' && Object.keys(o).every((k) => k === 'op' || k === 'id' || k === 'position'))

export interface LazyTryBackend extends FlowEditorBackend {
  /** 세션(과 사본)이 없으면 한 번만 만들고 사본 흐름 id 를 돌려준다. 세션 전 위치 이동도 이때 사본에 들어간다. */
  ensureSession(): Promise<string>
}

/**
 * 방문 즉시 편집기(#195, ADR-0032 초안): `/try?template=…` 는 세션 없이 템플릿 그래프로 편집기를 연다.
 * 세션은 「첫 쓰기」(위치만이 아닌 초안 저장·시험 대화·가입)에서 `POST /sessions/` 로 한 번 만들고(single-flight),
 * 그 뒤 연산은 기존 PATCH 로 보낸다(①a — 백엔드 변경 없음). 편집기 저장 큐가 직렬이라 큐 안의 생성은 저절로 한 번이고,
 * 큐 밖(가입)은 같은 `creating` 약속을 나눠 쓴다. 세션 전의 위치 이동은 `held` 에 두었다가 생성 직후 같은 순서로 보낸다.
 * 편집기는 경로 id 없이 쓰므로(`/try?template=`) 넘어오는 id 는 무시하고 여기 든 사본 id 를 쓴다.
 */
export function lazyTryBackend(template: string, hooks: { onSession?: (s: TrySessionOut) => void; onUnknown?: () => void } = {}): LazyTryBackend {
  let flowId: string | null = null
  let creating: Promise<string> | null = null
  let tpl: TryTemplateDetailOut | null = null
  const held: PatchOp[] = [] // 세션 전 위치 이동 — 지역 그래프(템플릿 + held)의 차이

  const patch = async (id: string, ops: PatchOp[]) => {
    const r = await appsTenantsTryApiTryPatchDraft(id, { ops } as unknown as DraftPatchIn)
    return { errors: r.data.errors as FlowError[] }
  }
  const create = (): Promise<string> => (creating ??= (async () => {
    const s = (await appsTenantsTryApiTryStart({ template })).data
    flowId = s.flow_id
    hooks.onSession?.(s)
    return s.flow_id
  })().catch((e: unknown) => { creating = null; throw e })) // 거절되면 다음 쓰기가 다시 시도한다
  const ensureSession = async (): Promise<string> => {
    const id = flowId ?? await create()
    if (held.length) await patch(id, held.splice(0))
    return id
  }

  return {
    ...tryBackend,
    ensureSession,
    loadFlow: async () => {
      if (!flowId) {
        // 새로고침: 같은 템플릿의 세션이 살아 있으면 서버 사본이 원본이다. 없거나 다른 템플릿이면 템플릿을 연다(세션 없음은 200 null).
        const s = (await appsTenantsTryApiTrySessionGet()).data
        if (s && s.template === template && !flowId) { flowId = s.flow_id; hooks.onSession?.(s) }
      }
      if (flowId) return tryBackend.loadFlow(flowId)
      try {
        tpl ??= (await appsTenantsTryApiTryTemplateDetail(template)).data
      } catch (e) {
        if (e instanceof HttpError && e.status === 404) hooks.onUnknown?.()
        throw e
      }
      return templateFlow(tpl, applyOps(tpl.graph as unknown as FlowGraph, held))
    },
    patchDraft: async (_id, ops) => {
      const list = ops as PatchOp[]
      if (!flowId && positionOnly(list)) { held.push(...list); return { errors: [] } } // 템플릿은 검증을 통과한 그래프다
      const id = flowId ?? await create()
      return patch(id, [...held.splice(0), ...list])
    },
    streamTest: async (_flowId, message, sessionId, onEvent) =>
      streamTestTurn(await ensureSession(), message, sessionId, onEvent, undefined, '/api/try'),
  }
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
