import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import * as api from '../api'
import { HttpError } from '../mutator'
import AgentsTab from './AgentsTab'

vi.mock('../api')

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listMembers).mockResolvedValue([
    { id: 'a1', user_id: 'u1', name: '김관리', email: 'kim@example.com', sub_tail: 'AAAAAA', is_active: true, role: 'admin' },
    { id: 'a2', user_id: 'u2', name: null, email: 'bob@example.com', sub_tail: 'BBBBBB', is_active: true, role: 'member' },
    { id: 'a3', user_id: 'u3', name: null, email: null, sub_tail: 'XYZ123', is_active: true, role: 'member' },
  ] as any)
  vi.mocked(api.listInvites).mockResolvedValue([])
  vi.mocked(api.memberMe).mockResolvedValue({ id: 'a1' } as any)
})

describe('AgentsTab — 팀원(멤버십)·초대 링크 (#40, #56)', () => {
  it('이름이 없으면 이메일, 이메일도 없으면 sub 끝 6자리로 보여 준다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    render(<AgentsTab />)

    expect(await screen.findByText('김관리')).toBeInTheDocument()
    expect(screen.getByText('bob@example.com')).toBeInTheDocument()
    expect(screen.getByText('계정 …XYZ123')).toBeInTheDocument()
    expect(await screen.findByText('(나)')).toBeInTheDocument()
  })

  it('관리자는 팀원을 관리자로 올릴 수 있다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    vi.mocked(api.changeMemberRole).mockResolvedValue({} as any)
    render(<AgentsTab />)

    const row = (await screen.findByText('bob@example.com')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: '관리자로' }))
    expect(api.changeMemberRole).toHaveBeenCalledWith('a2', 'admin')
  })

  it('마지막 관리자를 팀원으로 바꾸려 하면(409) 안내한다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    vi.mocked(api.changeMemberRole).mockRejectedValue(new HttpError(409, '/x'))
    render(<AgentsTab />)

    const row = (await screen.findByText('김관리')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: '팀원으로' }))
    expect(await screen.findByText(/마지막 관리자는 팀원으로 바꿀 수 없습니다/)).toBeInTheDocument()
  })

  it('팀원을 내보낼 때 확인을 받고, 마지막 관리자면(409) 안내한다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    vi.mocked(api.deactivateMember).mockRejectedValue(new HttpError(409, '/x'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<AgentsTab />)

    const row = (await screen.findByText('김관리')).closest('tr')!
    await userEvent.click(within(row).getByRole('button', { name: '내보내기' }))
    expect(window.confirm).toHaveBeenCalledWith('김관리님을 이 조직에서 내보내시겠습니까?')
    expect(api.deactivateMember).toHaveBeenCalledWith('a1')
    expect(await screen.findByText(/마지막 관리자는 내보낼 수 없습니다/)).toBeInTheDocument()
  })

  it('팀원은 관리 컨트롤을 보지 못한다(목록은 보임)', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    render(<AgentsTab />)

    expect(await screen.findByText('김관리')).toBeInTheDocument()
    expect(screen.getByText('팀원 관리는 관리자만 할 수 있습니다.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '초대 링크 만들기' })).toBeNull()
    expect(screen.queryByRole('button', { name: '내보내기' })).toBeNull()
    expect(api.listInvites).not.toHaveBeenCalled()
  })

  it('역할을 골라 초대 링크를 만들면 링크·만료를 한 번 보여 주고 복사할 수 있다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    vi.mocked(api.createInvite).mockResolvedValue({
      id: 'i1', role: 'admin', expires_at: '2026-10-31T00:00:00Z', created_at: '', token: 'tok',
      url: 'http://flow/api/auth/authorize/member?invite=tok',
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<AgentsTab />)

    await screen.findByText('김관리')
    await userEvent.selectOptions(screen.getByLabelText('역할'), 'admin')
    await userEvent.click(screen.getByRole('button', { name: '초대 링크 만들기' }))

    expect(api.createInvite).toHaveBeenCalledWith('admin')
    expect(await screen.findByLabelText('초대 링크')).toHaveValue('http://flow/api/auth/authorize/member?invite=tok')
    expect(screen.getByText(/관리자 초대 링크 · .*까지/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '복사' }))
    expect(writeText).toHaveBeenCalledWith('http://flow/api/auth/authorize/member?invite=tok')
    expect(await screen.findByRole('button', { name: '복사됨' })).toBeInTheDocument()
  })

  it('대기 중인 초대를 보여 주고 취소할 수 있다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('admin')
    vi.mocked(api.listInvites).mockResolvedValueOnce([
      { id: 'i9', role: 'member', expires_at: '2026-10-31T00:00:00Z', created_at: '' },
    ] as any).mockResolvedValue([])
    vi.mocked(api.revokeInvite).mockResolvedValue(undefined)
    render(<AgentsTab />)

    await userEvent.click(await screen.findByRole('button', { name: '초대 취소' }))
    expect(api.revokeInvite).toHaveBeenCalledWith('i9')
    expect(await screen.findByText('대기 중인 초대가 없습니다.')).toBeInTheDocument()
  })
})
