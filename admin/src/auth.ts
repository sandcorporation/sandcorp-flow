// 어드민 인증 클라이언트 (ADR-0013).
// access는 sessionStorage(단수명), refresh는 httpOnly 쿠키(JS가 못 읽음).
// authFetch가 401 시 1회 투명 refresh→재시도하고, 부팅 시 silent refresh로 access를 복구한다.

export type AuthKind = 'operator' | 'agent'

const BASE: string = import.meta.env.VITE_API_BASE || ''

const ACCESS_KEY: Record<AuthKind, string> = { operator: 'op_access', agent: 'agent_access' }
const REFRESH_PATH: Record<AuthKind, string> = {
  operator: '/api/operator/auth/refresh',
  agent: '/api/tenant/members/auth/refresh', // 멤버(고객) 테넌트 세션(#40)
}

// access 갱신 구독자(kind별). silent refresh로 토큰이 바뀌면 SSE 재오픈 등에 알린다.
const accessListeners: Record<AuthKind, Set<() => void>> = {
  operator: new Set(),
  agent: new Set(),
}

export function onAccessChange(kind: AuthKind, cb: () => void): () => void {
  accessListeners[kind].add(cb)
  return () => accessListeners[kind].delete(cb)
}

function notifyAccessChange(kind: AuthKind): void {
  for (const cb of accessListeners[kind]) cb()
}

export function getAccess(kind: AuthKind): string | null {
  return sessionStorage.getItem(ACCESS_KEY[kind])
}
export function setAccess(kind: AuthKind, token: string): void {
  sessionStorage.setItem(ACCESS_KEY[kind], token)
}
export function clearAccess(kind: AuthKind): void {
  sessionStorage.removeItem(ACCESS_KEY[kind])
}

// 진행 중인 refresh를 kind별로 공유한다(single-flight). 같은 access 만료 시점에 여러 요청이
// 동시에 401→refresh를 부르면, single-flight가 없을 경우 같은 refresh 쿠키로 여러 번 회전해
// 서버의 재사용 탐지가 Session Family를 폐기(=로그아웃)한다. 동시 호출은 한 번의 회전을 공유한다.
const inflightRefresh: Record<AuthKind, Promise<boolean> | null> = { operator: null, agent: null }

export function refresh(kind: AuthKind): Promise<boolean> {
  if (inflightRefresh[kind]) return inflightRefresh[kind]!
  const p = (async () => {
    try {
      const res = await fetch(`${BASE}${REFRESH_PATH[kind]}`, {
        method: 'POST',
        credentials: 'include', // refresh 쿠키 동봉
      })
      if (!res.ok) {
        clearAccess(kind)
        return false
      }
      const { access_token } = await res.json()
      setAccess(kind, access_token)
      notifyAccessChange(kind) // 새 토큰으로 갱신됨 → 구독자(SSE 등)에 통지
      return true
    } finally {
      inflightRefresh[kind] = null
    }
  })()
  inflightRefresh[kind] = p
  return p
}

export async function authFetch(kind: AuthKind, path: string, opts: RequestInit = {}): Promise<Response> {
  const send = () =>
    fetch(`${BASE}${path}`, {
      ...opts,
      credentials: 'include',
      headers: { ...(opts.headers || {}), Authorization: `Bearer ${getAccess(kind)}` },
    })

  let res = await send()
  if (res.status === 401 && (await refresh(kind))) {
    res = await send() // 새 access로 1회 재시도
  }
  return res
}

export async function bootSilentRefresh(kind: AuthKind): Promise<boolean> {
  if (getAccess(kind)) return true
  return refresh(kind)
}
