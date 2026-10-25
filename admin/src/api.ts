// admin HTTP facade (ADR-0014). CRUD는 전부 orval 생성 클라이언트(→ mutator)를 거친다.
// 손작성으로 남는 것: login/logout의 토큰 부수효과 + Operator 로그인 이동 + SSE(openEscalationStream).
// 이 facade는 친근한 함수명 + .data 언랩 + 일부 특수 계약(409/404/친절 에러)만 담당한다.
import { setAccess, clearAccess, getAccess, onAccessChange } from './auth'
import { HttpError } from './mutator'
import { getRole, registerRoleLoader, setRole } from './roleStore'

import {
  appsTenantsApiOperatorLogout,
  appsTenantsApiOperatorLogoutAll,
  appsTenantsApiListTenants,
  appsTenantsApiCreateTenant,
  appsTenantsApiDeleteTenant,
  appsTenantsApiSuspendTenant,
  appsTenantsApiOperatorAdminInvite,
} from './generated/endpoints/operator/operator'
import {
  appsTenantsApiGetConfig,
  appsTenantsApiGetTrial,
  appsTenantsApiUpdateConfig,
  appsTenantsApiResetTenantKey,
  appsTenantsApiUpdateSlug,
  appsTenantsApiProviderModels,
  appsTenantsApiProviderQuickSetup,
} from './generated/endpoints/tenant/tenant'
import {
  appsTenantsApiMemberLogout,
  appsTenantsApiMemberLogoutAll,
  appsTenantsApiListMembers,
  appsTenantsApiChangeMemberRole,
  appsTenantsApiDeactivateMember,
  appsTenantsApiMemberMe,
} from './generated/endpoints/tenant-members/tenant-members'
import {
  appsTenantsSecretsApiListSecrets,
  appsTenantsSecretsApiPutSecret,
  appsTenantsSecretsApiDeleteSecret,
} from './generated/endpoints/tenant-secrets/tenant-secrets'
import {
  appsTenantsSecretsApiListDomains,
  appsTenantsSecretsApiAddDomain,
  appsTenantsSecretsApiDeleteDomain,
} from './generated/endpoints/tenant-allowed-domains/tenant-allowed-domains'
import {
  appsTenantsApiListInvites,
  appsTenantsApiCreateTenantInvite,
  appsTenantsApiRevokeTenantInvite,
} from './generated/endpoints/tenant-invites/tenant-invites'
import {
  appsTenantsApiMemberTenants,
  appsTenantsApiMemberSelectTenant,
  appsTenantsApiMemberCreateTenant,
} from './generated/endpoints/auth/auth'
import { appsTenantsTryApiTryClaimFlow, appsTenantsTryApiTrySessionGet } from './generated/endpoints/try/try'
import {
  appsUsageApiTenantUsage,
  appsUsageApiOperatorUsage,
} from './generated/endpoints/usage/usage'
import type { TenantUsageOut, OperatorUsageOut } from './generated/model'
import {
  appsRagApiListDocuments,
  appsRagApiUploadDocument,
  appsRagApiUpdateDocument,
  appsRagApiDeleteDocument,
  appsRagApiListChunks,
  appsRagApiGraphSearch,
  appsRagApiGraphNeighbors,
  appsRagApiGraphStatus,
  appsRagApiRebuildGraph,
} from './generated/endpoints/rag/rag'
import {
  appsMemoryApiListVisitors,
  appsMemoryApiListVisitorSessions,
  appsMemoryApiListMemories,
  appsMemoryApiUpdateMemory,
  appsMemoryApiDeleteMemoryEntry,
} from './generated/endpoints/memory/memory'
import {
  appsMemoryApiGetSessionMessages,
  appsMemoryApiGetSessionCheckpoint,
  appsMemoryApiGetSessionRetrievals,
  appsMemoryApiListSessions,
  appsMemoryApiTakeoverSession,
} from './generated/endpoints/sessions/sessions'
import {
  appsEscalationApiListEscalations,
  appsEscalationApiClaimEscalation,
  appsEscalationApiSendMessage,
  appsEscalationApiGetEscalationMessages,
  appsEscalationApiResolveEscalation,
  appsEscalationApiSendTypingIndicator,
} from './generated/endpoints/escalations/escalations'

const BASE: string = import.meta.env.VITE_API_BASE || ''

// ── Operator ─────────────────────────────────────────────────────────────

// Operator 로그인은 sandcorp-auth(#21): 백엔드 /authorize로 이동 → sandcorp-auth → 백엔드 콜백이
// Refresh 쿠키를 심고 /admin-ui/operator로 돌려보낸다. access는 App 부팅의 silent refresh가 받는다.
// 거절이면 ?login_error=<사유>가 붙어 돌아온다(OperatorLogin이 거절 화면을 보여 준다).
export const OPERATOR_LOGIN_PATH = '/api/auth/authorize'
export function startOperatorLogin() {
  window.location.assign(`${BASE}${OPERATOR_LOGIN_PATH}`)
}
export async function operatorLogout() {
  await appsTenantsApiOperatorLogout()
  clearAccess('operator')
}
export async function operatorLogoutAll() {
  await appsTenantsApiOperatorLogoutAll()
  clearAccess('operator')
}
export async function listTenants() {
  return (await appsTenantsApiListTenants()).data
}
export async function createTenant(name: string) {
  return (await appsTenantsApiCreateTenant({ name })).data
}
export async function operatorAdminInvite(id: string) {
  // 백엔드가 응답 스키마를 선언하지 않아 생성 타입이 unknown이다 — 실제 응답은 {url}.
  return (await appsTenantsApiOperatorAdminInvite(id)).data as { url: string }
}
export async function suspendTenant(id: string) {
  return (await appsTenantsApiSuspendTenant(id)).data
}
export async function deleteTenant(id: string) {
  await appsTenantsApiDeleteTenant(id)
}

// ── 멤버(고객) 로그인 + 팀원·초대 관리 (#40) ─────────────────────────────────
// 로그인은 sandcorp-auth로 가는 브라우저 이동이다. 콜백이 멤버십 수에 따라 세션을 열거나
// ?select_tenant=1 / ?onboarding=1 / ?login_error= 를 붙여 /admin-ui/tenant 로 돌려보낸다.

export function memberLoginUrl(): string {
  return `${import.meta.env.VITE_API_BASE || ''}/api/auth/authorize/member`
}
// 「이 흐름으로 가입」(#42): 고객 로그인 뒤 옮기기 화면(/try/claim)으로 돌아온다 — 콜백은 언제나 표(ticket)를 준다.
export function claimLoginUrl(): string {
  return `${memberLoginUrl()}?then=try_claim`
}
/** 옮길 체험 세션이 살아 있으면 그 템플릿 id, 없으면(만료·이미 옮김) null. */
export async function trySessionTemplate(): Promise<string | null> {
  try { return (await appsTenantsTryApiTrySessionGet()).data.template } catch { return null }
}
export class ClaimError extends Error {
  constructor(message: string, public code?: string) {
    super(message)
    this.name = 'ClaimError'
  }
}
/** 체험 사본을 지금 고객 세션(Bearer)의 테넌트로 옮긴다. 쿠키(sf_try)는 브라우저가 싣는다 — /api/try 는 Bearer 를
 *  자동으로 싣지 않으므로 여기서 직접 붙인다. */
export async function claimTryFlow() {
  try {
    const access = getAccess('agent')
    return (await appsTenantsTryApiTryClaimFlow({ headers: access ? { Authorization: `Bearer ${access}` } : {} })).data
  } catch (e) {
    const body = (e instanceof HttpError ? e.body ?? {} : {}) as { detail?: string; code?: string }
    throw new ClaimError(body.detail || '흐름을 옮기지 못했습니다. 다시 시도하세요.', body.code)
  }
}
export async function memberTenants() {
  return (await appsTenantsApiMemberTenants()).data as {
    name: string | null; email: string | null; tenants: { tenant_id: string; tenant_name: string; role: string }[]
  }
}
export async function selectTenant(tenantId: string) {
  const data = (await appsTenantsApiMemberSelectTenant({ tenant_id: tenantId })).data as { access_token: string }
  setAccess('agent', data.access_token)
  return data
}
// 온보딩(#41): 조직을 만들고 첫 Admin 세션을 연다. 거절(400/409/429)이면 서버 문구를 담은 OnboardingError.
export class OnboardingError extends Error {
  constructor(message: string, public reason?: string) {
    super(message)
    this.name = 'OnboardingError'
  }
}
export async function createOrganization(name: string, slug: string) {
  try {
    const data = (await appsTenantsApiMemberCreateTenant({ name, slug })).data as {
      tenant_id: string; tenant_name: string; access_token: string
    }
    setAccess('agent', data.access_token)
    return data
  } catch (e) {
    if (e instanceof HttpError) {
      const body = (e.body ?? {}) as { detail?: string; reason?: string }
      if (e.status === 401) throw new OnboardingError('로그인이 만료되었습니다. 다시 로그인하세요.', 'expired')
      throw new OnboardingError(body.detail || '조직을 만들지 못했습니다. 다시 시도하세요.', body.reason)
    }
    throw e
  }
}
export type TrialState = 'byok' | 'trial' | 'exhausted' | 'unavailable' | 'platform_default'
export async function getTrial() {
  return (await appsTenantsApiGetTrial()).data as { state: TrialState; remaining: number; limit: number }
}
export async function agentLogout() {
  await appsTenantsApiMemberLogout()
  clearAccess('agent')
  setRole(null)
}
export async function agentLogoutAll() {
  await appsTenantsApiMemberLogoutAll()
  clearAccess('agent')
  setRole(null)
}
// sandcorp-auth 계정 관리 화면(이름·이메일·탈퇴는 거기서 한다).
export const ACCOUNT_URL = 'https://auth.sandcorp.co.kr/account'

export async function memberMe() {
  return (await appsTenantsApiMemberMe()).data
}
export async function listMembers() {
  return (await appsTenantsApiListMembers()).data
}
export async function changeMemberRole(memberId: string, role: 'admin' | 'member') {
  return (await appsTenantsApiChangeMemberRole(memberId, { role })).data
}
export async function deactivateMember(memberId: string) {
  return (await appsTenantsApiDeactivateMember(memberId)).data
}
export async function listInvites() {
  return (await appsTenantsApiListInvites()).data
}
export async function createInvite(role: 'admin' | 'member' = 'member') {
  return (await appsTenantsApiCreateTenantInvite({ role })).data as {
    id: string; role: string; expires_at: string; created_at: string; token: string; url: string
  }
}
export async function revokeInvite(inviteId: string) {
  await appsTenantsApiRevokeTenantInvite(inviteId)
}
// 현재 로그인한 멤버의 역할 — 서버 진실(members/me 의 DB 역할)을 읽어 둔 공용 상태(#120).
// 토큰 claims 의 role 은 발급 시점 값이라 쓰지 않는다. 아직 못 읽었으면 null(쓰기 컨트롤 숨김).
// 화면에서는 `useAgentRole()` 훅으로 읽는다(변경 시 다시 그려진다).
export function currentAgentRole(): 'admin' | 'member' | null {
  return getRole()
}
registerRoleLoader(async () => {
  if (!getAccess('agent')) return null
  const me = await memberMe()
  return me.role === 'admin' || me.role === 'member' ? me.role : null
})
// ── Tenant Config ─────────────────────────────────────────────────────────

export async function getTenantConfig() {
  return (await appsTenantsApiGetConfig()).data
}
export async function updateTenantConfig(data: any) {
  return (await appsTenantsApiUpdateConfig(data)).data
}
// OpenAI 키 1개로 LLM·Embedding·OCR 3종을 기본값으로 한 번에 설정한다(한방). 실패 시 throw.
export async function quickSetupOpenAI(apiKey: string) {
  return (await appsTenantsApiProviderQuickSetup({ api_key: apiKey })).data
}
export async function updateTenantSlug(slug: string) {
  try {
    return (await appsTenantsApiUpdateSlug({ slug })).data
  } catch (e) {
    throw new Error('slug 저장 실패 (형식·중복·예약어 확인)')
  }
}
export async function resetTenantKey() {
  try {
    return (await appsTenantsApiResetTenantKey()).data
  } catch (e) {
    throw new Error('재발급 실패')
  }
}

// provider의 사용가능 모델 목록을 폼 현재 값으로 조회한다(어드민 "모델 불러오기").
// 마스크 키(********)는 백엔드가 저장 키로 대체한다. 실패 시 throw(키/URL 오류).
export async function fetchProviderModels(
  kind: 'llm' | 'embed' | 'ocr',
  providerType: string,
  baseUrl: string,
  apiKey: string,
  model: string,
): Promise<string[]> {
  const res = await appsTenantsApiProviderModels({
    kind, type: providerType, base_url: baseUrl, api_key: apiKey, model,
  })
  return (res.data as { models: string[] }).models
}

// ── Documents / Graph ──────────────────────────────────────────────────────

export async function listDocuments() {
  return (await appsRagApiListDocuments()).data
}
export async function uploadDocument(file: File, name?: string) {
  return (await appsRagApiUploadDocument({ file, name })).data
}
export async function updateDocument(id: string, name: string) {
  return (await appsRagApiUpdateDocument(id, { name })).data
}
export async function deleteDocument(id: string) {
  await appsRagApiDeleteDocument(id)
}
export async function listDocumentChunks(docId: string) {
  return (await appsRagApiListChunks(docId)).data
}
export async function searchGraph(q: string) {
  return (await appsRagApiGraphSearch({ q })).data
}
export async function graphNeighbors(entity: string) {
  return (await appsRagApiGraphNeighbors({ entity })).data
}
export async function getGraphStatus() {
  return (await appsRagApiGraphStatus()).data
}
export async function rebuildGraph() {
  return (await appsRagApiRebuildGraph()).data
}

// ── Visitors / Memory / Sessions ───────────────────────────────────────────

export async function listVisitors(search?: string) {
  return (await appsMemoryApiListVisitors(search ? { search } : undefined)).data
}
export async function listVisitorSessions(visitorId: string) {
  return (await appsMemoryApiListVisitorSessions(visitorId)).data
}
export async function getSessionMessages(sessionId: string) {
  return (await appsMemoryApiGetSessionMessages(sessionId)).data
}
// 세션 콘솔: 전체 세션(escalation→활성→나머지) 목록(issue 139).
export async function listSessions(limit = 50, offset = 0) {
  return (await appsMemoryApiListSessions({ limit, offset })).data
}
// 임의 세션 takeover — 자동-claimed escalation 생성. 409(다른 상담원 점유) 정규화(issue 140).
export async function takeoverSession(
  sessionId: string,
): Promise<{ ok: boolean; status: number; escalation_id?: string }> {
  try {
    const r = await appsMemoryApiTakeoverSession(sessionId)
    return { ok: true, status: r.status, escalation_id: (r.data as { escalation_id: string }).escalation_id }
  } catch (e) {
    if (e instanceof HttpError) return { ok: false, status: e.status }
    throw e
  }
}
export async function getSessionCheckpoint(sessionId: string) {
  try {
    return (await appsMemoryApiGetSessionCheckpoint(sessionId)).data
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) return null
    throw e
  }
}
export type RetrievalTurn = { user_message: string; chunks: string[]; chunk_count: number; nodes: string[] }
export async function getSessionRetrievals(sessionId: string): Promise<RetrievalTurn[] | null> {
  try {
    const data = (await appsMemoryApiGetSessionRetrievals(sessionId)).data as { turns?: RetrievalTurn[] }
    return data?.turns ?? []
  } catch (e) {
    if (e instanceof HttpError && e.status === 404) return null
    throw e
  }
}
export async function listMemories(visitorId: string) {
  return (await appsMemoryApiListMemories(visitorId)).data
}
export async function updateMemory(visitorId: string, memoryId: string, data: { key: string; value: string }) {
  return (await appsMemoryApiUpdateMemory(visitorId, memoryId, data)).data
}
export async function deleteMemory(visitorId: string, memoryId: string) {
  await appsMemoryApiDeleteMemoryEntry(visitorId, memoryId)
}

// ── Escalations (HITL) ────────────────────────────────────────────────────

export async function listEscalations() {
  return (await appsEscalationApiListEscalations()).data
}
// claim은 409(이미 수락됨) 분기가 있어 status/ok로 정규화해 반환
export async function claimEscalation(escalationId: string): Promise<{ status: number; ok: boolean }> {
  try {
    const r = await appsEscalationApiClaimEscalation(escalationId)
    return { status: r.status, ok: true }
  } catch (e) {
    if (e instanceof HttpError) return { status: e.status, ok: false }
    throw e
  }
}
export async function sendEscalationMessage(escalationId: string, content: string) {
  return (await appsEscalationApiSendMessage(escalationId, { content })).data
}
export async function resolveEscalation(escalationId: string) {
  return (await appsEscalationApiResolveEscalation(escalationId)).data
}
export async function sendTypingIndicator(escalationId: string) {
  await appsEscalationApiSendTypingIndicator(escalationId)
}
export async function getEscalationMessages(escalationId: string) {
  return (await appsEscalationApiGetEscalationMessages(escalationId)).data
}

// ── SSE (손작성 — OpenAPI 모델링 불가) ──────────────────────────────────────
export interface StreamHandle {
  close: () => void
}
export function openEscalationStream(onEvent: (event: any) => void): StreamHandle {
  let es: EventSource | null = null
  const connect = () => {
    if (es) es.close()
    es = new EventSource(`${BASE}/api/tenant/escalations/stream?token=${getAccess('agent')}`)
    es.onmessage = (e) => onEvent(JSON.parse(e.data))
    es.addEventListener('hitl_new', (e) => onEvent({ ...JSON.parse((e as MessageEvent).data), type: 'hitl_new' }))
    es.addEventListener('hitl_claimed', (e) => onEvent({ ...JSON.parse((e as MessageEvent).data), type: 'hitl_claimed' }))
    es.addEventListener('hitl_resolved', (e) => onEvent({ ...JSON.parse((e as MessageEvent).data), type: 'hitl_resolved' }))
    es.addEventListener('visitor_message', (e) => onEvent({ ...JSON.parse((e as MessageEvent).data), type: 'visitor_message' }))
    // presence delta(issue 138) — 세션 콘솔 활성 계층 라이브 갱신
    es.addEventListener('session_connected', (e) => onEvent({ ...JSON.parse((e as MessageEvent).data), type: 'session_connected' }))
    es.addEventListener('session_disconnected', (e) => onEvent({ ...JSON.parse((e as MessageEvent).data), type: 'session_disconnected' }))
  }
  connect()
  const unsubscribe = onAccessChange('agent', connect)
  return {
    close() {
      if (es) es.close()
      unsubscribe()
    },
  }
}

// ── 토큰 사용량 (PRD-langfuse-token-tracking) ────────────────────────────────
export async function tenantUsage(days = 30): Promise<TenantUsageOut> {
  return (await appsUsageApiTenantUsage({ days })).data as TenantUsageOut
}
export async function operatorUsage(days = 30): Promise<OperatorUsageOut> {
  return (await appsUsageApiOperatorUsage({ days })).data as OperatorUsageOut
}

// --- Tenant Secret·허용 도메인 (#113) ---------------------------------------
// 실패는 사용자에게 보일 문구의 Error 로 바꿔 던진다(403 은 고정 문구, 422/409 는 서버 detail 그대로).
function secretsError(e: unknown, fallback: string): Error {
  if (e instanceof HttpError) {
    if (e.status === 403) return new Error('권한이 없습니다.')
    const detail = (e.body as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string' && detail) return new Error(detail)
  }
  return new Error(fallback)
}

export interface SecretItem { name: string; created_at: string; updated_at: string }
export interface AllowedDomainItem { id: string; pattern: string; created_at: string }

export async function listSecrets(): Promise<SecretItem[]> {
  return (await appsTenantsSecretsApiListSecrets()).data as SecretItem[]
}
/** 만들기·교체 같은 호출. 응답엔 값이 없고, 여기서도 값을 보관하지 않는다. */
export async function putSecret(name: string, value: string): Promise<void> {
  try { await appsTenantsSecretsApiPutSecret(name, { value }) }
  catch (e) { throw secretsError(e, '비밀을 저장하지 못했습니다.') }
}
export async function deleteSecret(name: string): Promise<void> {
  try { await appsTenantsSecretsApiDeleteSecret(name) }
  catch (e) { throw secretsError(e, '비밀을 지우지 못했습니다.') }
}
export async function listAllowedDomains(): Promise<AllowedDomainItem[]> {
  return (await appsTenantsSecretsApiListDomains()).data as AllowedDomainItem[]
}
export async function addAllowedDomain(pattern: string): Promise<AllowedDomainItem> {
  try { return (await appsTenantsSecretsApiAddDomain({ pattern })).data as AllowedDomainItem }
  catch (e) { throw secretsError(e, '도메인을 추가하지 못했습니다.') }
}
export async function deleteAllowedDomain(id: string): Promise<void> {
  try { await appsTenantsSecretsApiDeleteDomain(id) }
  catch (e) { throw secretsError(e, '도메인을 지우지 못했습니다.') }
}
