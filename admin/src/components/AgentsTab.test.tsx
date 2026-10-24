import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import * as api from '../api'
import { HttpError } from '../mutator'
import AgentsTab from './AgentsTab'

vi.mock('../api')

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listMembers).mockResolvedValue([
    { id: 'a1', user_id: 'u1', name: 'owner', email: null, is_active: true, role: 'admin' },
    { id: 'a2', user_id: 'u2', name: null, email: 'bob@example.com', is_active: true, role: 'member' },
  ] as any)
})

describe('AgentsTab — 팀원(멤버십)·초대 링크 (issue 210 → #40)', () => {
  it('Admin은 역할을 보고 멤버를 승격할 수 있다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    vi.mocked(api.changeMemberRole).mockResolvedValue({} as any)
    render(<AgentsTab />)

    expect(await screen.findByText('bob@example.com')).toBeInTheDocument()  // 이름이 없으면 이메일
    await userEvent.click(screen.getByRole('button', { name: 'Admin으로' }))  // bob(member) 행
    expect(api.changeMemberRole).toHaveBeenCalledWith('a2', 'admin')
  })

  it('마지막 Admin 강등(409)이면 안내를 보여준다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    vi.mocked(api.changeMemberRole).mockRejectedValue(new HttpError(409, '/x'))
    render(<AgentsTab />)

    await screen.findByText('owner')
    await userEvent.click(screen.getByRole('button', { name: 'Member로' }))  // owner(admin) 행
    expect(await screen.findByText('마지막 Admin은 강등할 수 없습니다.')).toBeInTheDocument()
  })

  it('Member는 팀원 관리 컨트롤을 보지 못한다(목록은 보임)', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    render(<AgentsTab />)

    expect(await screen.findByText('owner')).toBeInTheDocument()
    expect(screen.getByText('팀원 관리는 Admin만 할 수 있습니다.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '초대 링크 만들기' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Admin으로' })).toBeNull()
  })

  it('Admin이 역할을 골라 초대 링크를 만들면 링크를 한 번 보여준다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    vi.mocked(api.createInvite).mockResolvedValue({
      id: 'i1', role: 'admin', expires_at: '2026-10-31T00:00:00Z', created_at: '', token: 'tok',
      url: 'http://flow/api/auth/authorize?invite=tok',
    })
    render(<AgentsTab />)

    await screen.findByText('owner')
    await userEvent.selectOptions(screen.getByLabelText('역할'), 'admin')
    await userEvent.click(screen.getByRole('button', { name: '초대 링크 만들기' }))

    expect(api.createInvite).toHaveBeenCalledWith('admin')
    expect(await screen.findByLabelText('초대 링크')).toHaveValue('http://flow/api/auth/authorize?invite=tok')
    expect(screen.queryByPlaceholderText('현재 비밀번호')).toBeNull()  // 비밀번호 계정은 없다
  })
})
