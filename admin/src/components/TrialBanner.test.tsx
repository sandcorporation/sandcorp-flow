import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import * as api from '../api'
import TrialBanner from './TrialBanner'

vi.mock('../api')

function renderBanner() {
  return render(<MemoryRouter><TrialBanner /></MemoryRouter>)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.currentAgentRole).mockReturnValue('admin')
})

describe('TrialBanner — 체험 한도 안내 (#41)', () => {
  it('체험 중이면 남은 대화를 알리고 관리자에게 키 넣는 곳을 잇는다', async () => {
    vi.mocked(api.getTrial).mockResolvedValue({ state: 'trial', remaining: 37, limit: 200 })
    renderBanner()
    expect(await screen.findByRole('status')).toHaveTextContent('남은 대화 37/200턴')
    expect(screen.getByRole('link', { name: 'LLM 키 넣기' })).toHaveAttribute('href', '/tenant/config?section=ai')
  })

  it('다 썼으면 경고 배너로 LLM 키를 넣으라고 한다', async () => {
    vi.mocked(api.getTrial).mockResolvedValue({ state: 'exhausted', remaining: 0, limit: 200 })
    renderBanner()
    expect(await screen.findByRole('alert')).toHaveTextContent('체험 한도를 다 썼습니다 — 설정에서 LLM 키를 넣어 주세요')
  })

  it('플랫폼 키가 아직 없으면 준비되지 않았다고 알린다', async () => {
    vi.mocked(api.getTrial).mockResolvedValue({ state: 'unavailable', remaining: 200, limit: 200 })
    renderBanner()
    expect(await screen.findByRole('alert')).toHaveTextContent('플랫폼 체험이 준비되지 않았습니다')
  })

  it('팀원은 키 넣는 링크 대신 관리자에게 알리라고 본다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    vi.mocked(api.getTrial).mockResolvedValue({ state: 'exhausted', remaining: 0, limit: 200 })
    renderBanner()
    expect(await screen.findByText('관리자에게 알려 주세요.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'LLM 키 넣기' })).toBeNull()
  })

  it.each(['byok', 'platform_default'] as const)('%s면 아무것도 보이지 않는다', async (state) => {
    vi.mocked(api.getTrial).mockResolvedValue({ state, remaining: 200, limit: 200 })
    const { container } = renderBanner()
    await Promise.resolve()
    expect(container).toBeEmptyDOMElement()
  })
})
