// 화면이 쓰는 「내 역할」의 단일 출처(#120). 값은 서버 진실(/api/tenant/members/me 의 DB 역할)이고,
// 토큰 claims 의 role 은 발급 시점 값이라 강등·승격 뒤에는 낡을 수 있어 쓰지 않는다.
// 읽기 전에는 null(= 쓰기 컨트롤 숨김). api.ts 가 로더를 등록한다(순환 import 방지).
export type AgentRole = 'admin' | 'member'

let role: AgentRole | null = null
let inflight: Promise<void> | null = null
let loader: (() => Promise<AgentRole | null>) | null = null
const listeners = new Set<() => void>()

export function getRole(): AgentRole | null { return role }
export function setRole(next: AgentRole | null) {
  if (next === role) return
  role = next
  listeners.forEach((l) => l())
}
export function subscribeRole(l: () => void): () => void {
  listeners.add(l)
  return () => { listeners.delete(l) }
}
export function registerRoleLoader(fn: () => Promise<AgentRole | null>) { loader = fn }

// 서버에서 역할을 (다시) 읽는다. 동시에 여러 번 불려도 요청은 하나. 실패하면 지금 값을 그대로 둔다.
export function reloadRole(): Promise<void> {
  if (!loader) return Promise.resolve()
  if (!inflight) {
    inflight = loader().then((r) => { if (r) setRole(r) }, () => {}).finally(() => { inflight = null })
  }
  return inflight
}
// 아직 안 읽었으면 읽는다(화면 진입·새로고침 때 한 번).
export function ensureRole(): Promise<void> {
  return role === null ? reloadRole() : Promise.resolve()
}
