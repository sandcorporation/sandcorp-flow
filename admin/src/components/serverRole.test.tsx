// #120: 화면의 역할 판단은 토큰 claims 가 아니라 서버 진실(/api/tenant/members/me)이다.
// api·화면은 실제를 쓰고 네트워크 경계(fetch)만 대역으로 바꾼다.
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import ConfigTab from './ConfigTab'
import AgentsTab from './AgentsTab'
import FlowsTab from './FlowsTab'
import { setRole } from '../roleStore'

function jwt(role: string): string {
  return `h.${btoa(JSON.stringify({ role }))}.s`
}
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

/** serverRole 은 호출 사이에 바꿀 수 있다(강등 흉내). writeStatus 가 있으면 POST/PUT/DELETE 를 그 상태로 막는다. */
function stubServer(state: { serverRole: 'admin' | 'member'; writeStatus?: number }) {
  const calls: string[] = []
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push(`${method} ${path}`)
    if (method !== 'GET' && state.writeStatus) return json(state.writeStatus, { detail: '권한이 없습니다.' })
    if (path.endsWith('/api/tenant/members/me')) return json(200, { id: 'm1', tenant_id: 't1', tenant_name: '조직', role: state.serverRole, name: '나', email: 'me@x.kr' })
    if (path.endsWith('/api/tenant/config/')) return json(200, { model_id: 'm' })
    if (path.endsWith('/api/tenant/secrets/')) return json(200, [{ name: 'CRM_TOKEN', created_at: '', updated_at: '' }])
    if (path.endsWith('/api/tenant/allowed-domains/')) return json(200, [{ id: 'd1', pattern: 'a.example.com', created_at: '' }])
    if (path.endsWith('/api/tenant/members/')) return json(200, [{ id: 'm1', name: '나', email: 'me@x.kr', role: state.serverRole, is_active: true }])
    if (path.includes('/api/tenant/members/invites')) return json(200, [])
    if (path.endsWith('/api/tenant/flows/')) return json(200, [])
    return json(200, [])
  })
  return calls
}

beforeEach(() => {
  setRole(null)
  sessionStorage.clear()
  sessionStorage.setItem('agent_access', jwt('admin')) // 로그인 때 발급된(낡은) 토큰은 admin
})
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })

const cfg = () => render(<MemoryRouter initialEntries={['/tenant/config?section=secrets']}><ConfigTab /></MemoryRouter>)

describe('역할은 서버 진실(members/me)에서 읽는다', () => {
  it('토큰은 admin 이어도 서버가 member 면 비밀·허용 도메인 탭에 쓰기 컨트롤이 없고 목록은 보인다', async () => {
    stubServer({ serverRole: 'member' })
    cfg()
    expect(await screen.findByText('CRM_TOKEN')).toBeInTheDocument()
    expect(screen.getByText('a.example.com')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText(/관리자만/)).toHaveLength(2))
    expect(screen.queryByLabelText('비밀 값')).toBeNull()
    expect(screen.queryByRole('button', { name: /삭제|값 바꾸기|추가|비밀 저장/ })).toBeNull()
  })

  it('서버가 admin 이면 쓰기 컨트롤이 보인다', async () => {
    sessionStorage.setItem('agent_access', jwt('member')) // 낡은 토큰이 member 여도 서버 역할이 이긴다
    stubServer({ serverRole: 'admin' })
    cfg()
    expect(await screen.findByLabelText('비밀 값')).toBeInTheDocument()
  })

  it('역할을 읽기 전(첫 렌더 직후)에는 쓰기 컨트롤이 보이지 않는다', async () => {
    stubServer({ serverRole: 'admin' })
    cfg()
    expect(screen.queryByLabelText('비밀 값')).toBeNull()
    expect(await screen.findByLabelText('비밀 값')).toBeInTheDocument()
  })

  it('팀원 화면도 서버가 member 면 초대·작업 컨트롤이 없다', async () => {
    stubServer({ serverRole: 'member' })
    render(<MemoryRouter><AgentsTab /></MemoryRouter>)
    expect(await screen.findByText(/팀원 관리는 관리자만/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /초대/ })).toBeNull()
  })

  it('흐름 목록도 서버가 member 면 「새 흐름」이 없다', async () => {
    const calls = stubServer({ serverRole: 'member' })
    render(<MemoryRouter><FlowsTab /></MemoryRouter>)
    await waitFor(() => expect(calls.some((c) => c.endsWith('/members/me'))).toBe(true))
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByText(/새 흐름/)).toBeNull()
  })

  it('쓰기가 403 이면 역할을 다시 읽어 화면을 갱신한다(서버에서 강등된 경우)', async () => {
    const state: { serverRole: 'admin' | 'member'; writeStatus?: number } = { serverRole: 'admin' }
    stubServer(state)
    cfg()
    const pattern = await screen.findByLabelText('허용 도메인 패턴')
    state.serverRole = 'member' // 화면을 연 뒤 다른 곳에서 강등됨
    state.writeStatus = 403
    await userEvent.type(pattern, 'b.example.com')
    await userEvent.click(screen.getByRole('button', { name: '도메인 추가' }))
    await waitFor(() => expect(screen.queryByLabelText('허용 도메인 패턴')).toBeNull())
    expect(screen.getAllByText(/관리자만/).length).toBeGreaterThan(0)
  })
})
