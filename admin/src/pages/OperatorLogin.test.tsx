import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import * as api from '../api'
import OperatorLogin from './OperatorLogin'

vi.mock('../api')

beforeEach(() => { vi.clearAllMocks() })

function arriveAt(path: string) {
  render(<MemoryRouter initialEntries={[path]}><OperatorLogin /></MemoryRouter>)
}

describe('OperatorLogin — sandcorp-auth 로그인 (#21)', () => {
  it('비밀번호 폼 없이 sandcorp 로그인 버튼만 보이고, 누르면 로그인을 시작한다', async () => {
    arriveAt('/operator')

    expect(screen.queryByPlaceholderText('비밀번호')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('아이디')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'sandcorp 계정으로 로그인' }))

    expect(api.startOperatorLogin).toHaveBeenCalledTimes(1)
  })

  it('일반 사용자로 돌아온 콜백은 거절 화면을 보여 준다', () => {
    arriveAt('/operator?login_error=member')

    expect(screen.getByText('로그인할 수 없습니다')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('대표 계정만')
  })

  it('admin 권한이 없는 대표 토큰은 권한 안내를 보여 준다', () => {
    arriveAt('/operator?login_error=admin')

    expect(screen.getByRole('alert')).toHaveTextContent('admin')
  })

  it.each([
    ['state', '만료'],
    ['denied', '취소'],
    ['token', '확인하지 못했습니다'],
    ['config', 'AUTH_CLIENT_ID'],
    ['something-new', '다시 시도하세요'],
  ])('사유 %s를 사람이 읽을 말로 보여 준다', (reason, text) => {
    arriveAt(`/operator?login_error=${reason}`)

    expect(screen.getByRole('alert')).toHaveTextContent(text)
  })

  it('거절 화면에서 다시 로그인을 시작할 수 있다', async () => {
    arriveAt('/operator?login_error=state')

    await userEvent.click(screen.getByRole('button', { name: '다시 로그인' }))

    expect(api.startOperatorLogin).toHaveBeenCalledTimes(1)
  })
})
