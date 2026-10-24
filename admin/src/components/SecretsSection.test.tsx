import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import ConfigTab from './ConfigTab'
import * as api from '../api'

vi.mock('../api')

const SECRET_VALUE = 'sk-live-ABCDEFGH-12345678'

function renderSecrets() {
  return render(
    <MemoryRouter initialEntries={['/tenant/config?section=secrets']}>
      <ConfigTab />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getTenantConfig).mockResolvedValue({ model_id: 'm' } as any)
  vi.mocked(api.currentAgentRole).mockReturnValue('admin')
  vi.mocked(api.listSecrets).mockResolvedValue([])
  vi.mocked(api.listAllowedDomains).mockResolvedValue([])
  vi.mocked(api.putSecret).mockResolvedValue()
  vi.mocked(api.deleteSecret).mockResolvedValue()
  vi.mocked(api.addAllowedDomain).mockResolvedValue({ id: 'd1', pattern: 'api.example.com', created_at: '' })
  vi.mocked(api.deleteAllowedDomain).mockResolvedValue()
})

describe('ConfigTab — 비밀·허용 도메인', () => {
  it('비밀 추가: 값 입력란은 password형, 저장하면 이름만 남고 값은 DOM·입력란 어디에도 없다', async () => {
    vi.mocked(api.listSecrets)
      .mockResolvedValueOnce([])
      .mockResolvedValue([{ name: 'CRM_TOKEN', created_at: '', updated_at: '' }])
    const { container } = renderSecrets()
    const value = await screen.findByLabelText('비밀 값')
    expect(value).toHaveAttribute('type', 'password')
    await userEvent.type(screen.getByLabelText('비밀 이름'), 'CRM_TOKEN')
    await userEvent.type(value, SECRET_VALUE)
    await userEvent.click(screen.getByRole('button', { name: '비밀 저장' }))
    expect(api.putSecret).toHaveBeenCalledWith('CRM_TOKEN', SECRET_VALUE)
    expect(await screen.findByText('CRM_TOKEN')).toBeInTheDocument()
    expect(screen.getByLabelText('비밀 값')).toHaveValue('')
    expect(screen.getByLabelText('비밀 이름')).toHaveValue('')
    expect(container.innerHTML).not.toContain(SECRET_VALUE)
  })

  it('저장 실패(422) 문구를 그대로 보이고 값은 입력란에 남기지 않는다', async () => {
    vi.mocked(api.putSecret).mockRejectedValue(new Error('값은 8자 이상이어야 합니다.'))
    const { container } = renderSecrets()
    await userEvent.type(await screen.findByLabelText('비밀 이름'), 'X')
    await userEvent.type(screen.getByLabelText('비밀 값'), SECRET_VALUE)
    await userEvent.click(screen.getByRole('button', { name: '비밀 저장' }))
    expect(await screen.findByText('값은 8자 이상이어야 합니다.')).toBeInTheDocument()
    expect(container.innerHTML).not.toContain(SECRET_VALUE)
  })

  it('값 바꾸기: 이름 옆 버튼으로 새 값만 받아 같은 이름으로 PUT 한다', async () => {
    vi.mocked(api.listSecrets).mockResolvedValue([{ name: 'CRM_TOKEN', created_at: '', updated_at: '' }])
    const { container } = renderSecrets()
    await userEvent.click(await screen.findByRole('button', { name: 'CRM_TOKEN 값 바꾸기' }))
    const input = screen.getByLabelText('CRM_TOKEN 새 값')
    expect(input).toHaveAttribute('type', 'password')
    await userEvent.type(input, SECRET_VALUE)
    await userEvent.click(screen.getByRole('button', { name: '교체' }))
    expect(api.putSecret).toHaveBeenCalledWith('CRM_TOKEN', SECRET_VALUE)
    await waitFor(() => expect(screen.queryByLabelText('CRM_TOKEN 새 값')).toBeNull())
    expect(container.innerHTML).not.toContain(SECRET_VALUE)
  })

  it('비밀 삭제는 확인 한 번 뒤에만 지운다', async () => {
    vi.mocked(api.listSecrets).mockResolvedValue([{ name: 'CRM_TOKEN', created_at: '', updated_at: '' }])
    renderSecrets()
    await userEvent.click(await screen.findByRole('button', { name: 'CRM_TOKEN 삭제' }))
    expect(api.deleteSecret).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '삭제 확인' }))
    expect(api.deleteSecret).toHaveBeenCalledWith('CRM_TOKEN')
  })

  it('허용 도메인 추가·삭제, 와일드카드 설명, 서버 422 문구 표시', async () => {
    vi.mocked(api.listAllowedDomains).mockResolvedValue([{ id: 'd9', pattern: '*.example.com', created_at: '' }])
    renderSecrets()
    expect(await screen.findByText('*.example.com')).toBeInTheDocument()
    expect(screen.getByText(/\*\.example\.com.*하위/)).toBeInTheDocument()
    vi.mocked(api.addAllowedDomain).mockRejectedValueOnce(new Error('IP 주소는 허용 도메인이 될 수 없습니다.'))
    await userEvent.type(screen.getByLabelText('허용 도메인 패턴'), '10.0.0.1')
    await userEvent.click(screen.getByRole('button', { name: '도메인 추가' }))
    expect(await screen.findByText('IP 주소는 허용 도메인이 될 수 없습니다.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '*.example.com 삭제' }))
    expect(api.deleteAllowedDomain).toHaveBeenCalledWith('d9')
  })

  it('Member 는 목록만 보고 쓰기 컨트롤이 없다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    vi.mocked(api.listSecrets).mockResolvedValue([{ name: 'CRM_TOKEN', created_at: '', updated_at: '' }])
    vi.mocked(api.listAllowedDomains).mockResolvedValue([{ id: 'd9', pattern: 'a.example.com', created_at: '' }])
    renderSecrets()
    expect(await screen.findByText('CRM_TOKEN')).toBeInTheDocument()
    expect(screen.getByText('a.example.com')).toBeInTheDocument()
    expect(screen.queryByLabelText('비밀 값')).toBeNull()
    expect(screen.queryByRole('button', { name: /삭제|값 바꾸기|추가|비밀 저장/ })).toBeNull()
    expect(screen.getByText(/관리자만/)).toBeInTheDocument()
  })

  it('쓰기가 403 이면 「권한이 없습니다.」를 보인다', async () => {
    vi.mocked(api.addAllowedDomain).mockRejectedValue(new Error('권한이 없습니다.'))
    renderSecrets()
    await userEvent.type(await screen.findByLabelText('허용 도메인 패턴'), 'a.com')
    await userEvent.click(screen.getByRole('button', { name: '도메인 추가' }))
    expect(await screen.findByText('권한이 없습니다.')).toBeInTheDocument()
  })
})
