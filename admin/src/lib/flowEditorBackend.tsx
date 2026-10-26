// 편집기가 기대는 「바깥」을 한 자리에 모은다(#37). 로그인한 테넌트 편집기와 로그인 없는 체험(/try)이 같은
// FlowEditorPage·TestPanel 을 쓰되, 어느 경로·인증으로 부르는지와 어떤 UI 를 보이는지는 여기서만 갈린다.
// 편집기 코드에 「체험이면…」을 흩뿌리지 않고, 화면 쪽(TryFlowPage)이 Provider 로 체험 백엔드를 끼운다.
import { createContext, useContext } from 'react'
import { appsFlowsApiFlowsGet, appsFlowsApiFlowsGetVersion, appsFlowsApiFlowsNodeSchema, appsFlowsApiFlowsPatchDraft } from '../generated/endpoints/flows/flows'
import { appsTenantsApiGetConfig } from '../generated/endpoints/tenant/tenant'
import { appsTenantsApiMemberMe } from '../generated/endpoints/tenant-members/tenant-members'
import type { PublicFlowOut } from '../generated/model'
import { appsTenantsTryApiTryFlowGet, appsTenantsTryApiTryNodeSchema, appsTenantsTryApiTryPatchDraft, appsTenantsTryApiTryPutDraft, appsTenantsTryApiTrySessionGet, appsTenantsTryApiTryStart, appsTenantsTryApiTryTemplateDetail } from '../generated/endpoints/try/try'
import type { DraftPatchIn, DraftPutInGraph, FlowDetailOut, TrySessionOut, TryStartInGraph, TryTemplateDetailOut } from '../generated/model'
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
  /** 저장됨 상태에서 머리줄에 보일 문구를 바꾼다(null/없음 = 「초안 저장됨」). 세션 전 지연 생성 편집기가 쓴다(#196). */
  savedLabel?(): string | null
  /** 저장이 거절된 뒤 편집기가 서버 초안으로 되돌리지 않고 지역 편집을 그대로 두는가(세션 전 첫 저장 거절). */
  keepsEditsOnFailure?(): boolean
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

/** 읽기 전용 백엔드의 바탕: 편집·시험 대화 없음. 공개 흐름 보기(#39)가 상속한다(체험 「흐름 보기」는 #199 에서 없앴다). */
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
  /** 세션(과 사본)이 없으면 한 번만 만들고 사본 흐름 id 를 돌려준다. 그때까지 고친 지역 그래프가 통째로 들어간다. */
  ensureSession(): Promise<string>
}

export interface LazyTryHooks {
  onSession?: (s: TrySessionOut) => void
  onUnknown?: () => void
  /** 다른 템플릿의 체험이 진행 중이다 — 여기서 고치면 그 체험이 새 체험으로 바뀐다(#197). */
  onOtherSession?: (otherTemplate: string) => void
  /** 편집 중 세션이 사라졌다(24시간 만료 등). 지역 모드로 돌아갔고 다음 쓰기가 새 세션을 만든다(#197, Q5). */
  onExpired?: () => void
}

/** 세션 전 지역 그래프를 새로고침 너머로 지키는 자리(#197). 저장소 접근이 던지면(차단·비공개 창) 메모리만 쓴다. */
const draftKey = (template: string) => `sandcorp-flow:try-draft:${template}`
export function readDraft(template: string): FlowGraph | null {
  try {
    const raw = sessionStorage.getItem(draftKey(template))
    const g = raw ? JSON.parse(raw) as Partial<FlowGraph> : null
    return g && Array.isArray(g.nodes) && Array.isArray(g.edges) ? g as FlowGraph : null
  } catch { return null }
}
function writeDraft(template: string, g: FlowGraph | null): void {
  try {
    if (g) sessionStorage.setItem(draftKey(template), JSON.stringify(g))
    else sessionStorage.removeItem(draftKey(template))
  } catch { /* 메모리만 */ }
}

/** 서버 세션이 없어졌다는 응답인가(쿠키 없음·만료 401, 사본 404). */
const sessionGone = (e: unknown): boolean =>
  e instanceof HttpError && (e.status === 404 || (e.status === 401 && (e.body as { code?: string } | undefined)?.code === 'try_no_session'))

/**
 * 방문 즉시 편집기(#195~#197, ADR-0032 초안): `/try?template=…` 는 세션 없이 템플릿 그래프로 편집기를 연다.
 * 세션은 「첫 쓰기」(위치만이 아닌 초안 저장·시험 대화·가입)에서 `POST /sessions/ {template, graph}` 로 한 번 만든다(single-flight).
 * 세션 전의 편집은 **지역 그래프**(`local` = 템플릿 + 지금까지의 연산)에 쌓이고, 생성은 그 지역 그래프를 통째로 싣는다(①b) —
 * 위치 이동도 거기 담긴다(Q2). 지역 그래프는 sessionStorage 에도 보관해 새로고침 뒤 그것으로 연다(세션이 생기면 지운다).
 * 생성이 거절되면(IP 한도 등) 지역 그래프는 그대로라 화면이 되돌아가지 않고, 다음 쓰기가 같은 그래프로 다시 시도한다(연산 재생 없음).
 * 생성 직전에 `GET /session/` 으로 한 번 확인한다(Q6): 같은 템플릿의 세션이 이미 있으면(다른 탭) POST 없이 그것을 쓰고
 * 지역 그래프로 사본 초안을 덮는다(내가 방금 고친 편집이 이긴다). 편집 중 세션이 사라지면(Q5) 지역 모드로 돌아가 안내만 하고 다음 쓰기에 새로 만든다.
 * 생성 중에 들어온 연산은 `pending` 에 남아 생성 직후 PATCH 로 따라간다.
 * 편집기는 경로 id 없이 쓰므로(`/try?template=`) 넘어오는 id 는 무시하고 여기 든 사본 id 를 쓴다.
 */
export function lazyTryBackend(template: string, hooks: LazyTryHooks = {}): LazyTryBackend {
  let flowId: string | null = null
  let creating: Promise<string> | null = null
  let local: FlowGraph | null = null // 지역 그래프(템플릿 + 연산). 세션이 있는 동안에도 따라가 두어 만료 때 그대로 지역 모드로 돌아간다
  let name = ''
  const pending: PatchOp[] = [] // 지역 그래프에는 들어갔지만 서버 초안에는 아직 안 간 연산
  let lastErrors: FlowError[] = []

  const setLocal = (g: FlowGraph) => { local = g; if (!flowId) writeDraft(template, g) }
  const patch = async (id: string, ops: PatchOp[]) => {
    const r = await appsTenantsTryApiTryPatchDraft(id, { ops } as unknown as DraftPatchIn)
    return { errors: r.data.errors as FlowError[] }
  }
  const create = (): Promise<string> => (creating ??= (async () => {
    const sent = pending.length // 이 그래프에 이미 담긴 연산 수(생성 중에 더 쌓일 수 있다)
    const existing = (await appsTenantsTryApiTrySessionGet().catch(() => null))?.data
    if (existing && existing.template === template) {
      // 다른 탭이 먼저 만들었다 — 새로 만들지 않고(IP 한도 낭비·두 사본 방지) 그 사본을 내 지역 그래프로 덮는다.
      const r = await appsTenantsTryApiTryPutDraft(existing.flow_id, { graph: local as unknown as DraftPutInGraph })
      flowId = existing.flow_id
      lastErrors = (r.data.errors ?? []) as FlowError[]
      pending.splice(0, sent)
      writeDraft(template, null)
      hooks.onSession?.(existing)
      return existing.flow_id
    }
    const s = (await appsTenantsTryApiTryStart({ template, graph: local as unknown as TryStartInGraph })).data
    flowId = s.flow_id
    pending.splice(0, sent)
    lastErrors = (s.draft?.errors ?? []) as FlowError[]
    writeDraft(template, null)
    hooks.onSession?.(s)
    return s.flow_id
  })().catch((e: unknown) => { creating = null; throw e })) // 거절되면 다음 쓰기가 같은 지역 그래프로 다시 시도한다
  /** 세션이 사라졌다: 지역 모드로 돌아가 편집(= local)을 지키고 안내한다. 다음 쓰기가 새 세션을 만든다. */
  const expire = () => {
    if (!flowId) return
    flowId = null; creating = null; pending.length = 0
    if (local) writeDraft(template, local)
    hooks.onExpired?.()
  }
  const loadLocal = async (): Promise<FlowGraph> => {
    if (!local) {
      const tpl = (await appsTenantsTryApiTryTemplateDetail(template)).data
      name = tpl.name
      local = readDraft(template) ?? (tpl.graph as unknown as FlowGraph) // 새로고침 전에 고치던 그래프가 있으면 그것으로
    }
    return local
  }
  const ensureSession = async (): Promise<string> => {
    if (!flowId) await loadLocal() // 시험 대화·가입이 첫 쓰기여도 보낼 지역 그래프(템플릿)가 있어야 한다
    try {
      const id = flowId ?? await create()
      if (pending.length) lastErrors = (await patch(id, pending.splice(0))).errors
      return id
    } catch (e) { if (sessionGone(e)) expire(); throw e }
  }

  return {
    ...tryBackend,
    ensureSession,
    loadFlow: async () => {
      if (!flowId) {
        // 새로고침: 같은 템플릿의 세션이 살아 있고 보관된 지역 편집이 없으면 서버 사본이 원본이다. 보관본이 있으면 그것이 이기고(첫 쓰기가 사본을 덮는다)
        // 없거나 다른 템플릿이면 템플릿을 연다(세션 없음은 200 null). 다른 템플릿의 진행 중 체험은 알린다.
        const s = (await appsTenantsTryApiTrySessionGet()).data
        if (s && s.template === template) { if (!readDraft(template) && !flowId) { flowId = s.flow_id; hooks.onSession?.(s) } }
        else if (s) hooks.onOtherSession?.(s.template)
      }
      if (flowId) {
        try {
          const f = await tryBackend.loadFlow(flowId)
          local = f.draft.graph as unknown as FlowGraph
          name = f.flow.name
          return f
        } catch (e) { if (!sessionGone(e)) throw e; expire() }
      }
      try {
        const graph = await loadLocal()
        return templateFlow({ id: template, name } as TryTemplateDetailOut, graph)
      } catch (e) {
        if (e instanceof HttpError && e.status === 404) hooks.onUnknown?.()
        throw e
      }
    },
    patchDraft: async (_id, ops) => {
      const list = ops as PatchOp[]
      try { setLocal(applyOps(await loadLocal(), list)) } catch (e) { if (!flowId) throw e } // 세션이 있으면 서버가 원본이라 지역 사본은 거들 뿐
      pending.push(...list)
      if (!flowId && positionOnly(list)) return { errors: [] } // 템플릿은 검증을 통과한 그래프다
      await ensureSession()
      return { errors: lastErrors }
    },
    streamTest: async (_flowId, message, sessionId, onEvent) => {
      const id = await ensureSession()
      try { await streamTestTurn(id, message, sessionId, onEvent, undefined, '/api/try') } catch (e) { if (sessionGone(e)) expire(); throw e }
    },
    savedLabel: () => (flowId ? null : '첫 저장 때 체험이 시작돼요'),
    keepsEditsOnFailure: () => !flowId,
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
