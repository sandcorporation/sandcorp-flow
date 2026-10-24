// orval custom instance (ADR-0014). 생성된 모든 클라이언트 함수가 이걸 거친다.
// orval `client: fetch` 계약: customInstance<T>(url, init) → Promise<{data,status,headers}>.
// ADR-0013의 authFetch 로직을 흡수하되, URL 프리픽스로 kind를 판정한다.

import { AuthKind, getAccess, refresh } from './auth'
import { reloadRole } from './roleStore'

const BASE: string = import.meta.env.VITE_API_BASE || ''

export class HttpError extends Error {
  constructor(public status: number, public url: string, public body?: unknown) {
    super(`HTTP ${status} ${url}`)
    this.name = 'HttpError'
  }
}

function kindForUrl(url: string): AuthKind {
  // 라우터 마운트와 일치: /api/operator/* → operator, 그 외(/api/tenant/*) → agent
  return url.startsWith('/api/operator') ? 'operator' : 'agent'
}

async function parseBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined
  const text = await res.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return text // 에러 응답 등 비-JSON body는 원문 그대로
  }
}

export async function customInstance<T>(url: string, init: RequestInit = {}): Promise<T> {
  const kind = kindForUrl(url)
  const fullUrl = `${BASE}${url}`
  const isForm = init.body instanceof FormData

  const send = (): Promise<Response> => {
    const headers = new Headers(init.headers)
    const access = getAccess(kind)
    if (access) headers.set('Authorization', `Bearer ${access}`)
    if (isForm) headers.delete('Content-Type') // multipart boundary는 브라우저가 설정
    return fetch(fullUrl, { ...init, credentials: 'include', headers }) // refresh 쿠키 동봉
  }

  let res = await send()
  if (res.status === 401 && (await refresh(kind))) {
    res = await send() // 새 access로 1회 재시도
  }
  const data = await parseBody(res)
  if (!res.ok) {
    // 테넌트 쓰기가 403 이면 역할이 바뀌었을 수 있다 — 서버 역할을 다시 읽어 화면을 맞춘다(#120).
    if (res.status === 403 && kind === 'agent' && !url.endsWith('/members/me')) void reloadRole()
    throw new HttpError(res.status, fullUrl, data)
  }
  // orval fetch 클라이언트가 기대하는 응답 래퍼
  return { data, status: res.status, headers: res.headers } as T
}

export default customInstance
