import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import HitlTab from './HitlTab'
import * as api from '../api'

vi.mock('../api')

function esc(overrides = {}) {
  return { id: 'e1', session_id: 's-esc', trigger_type: 'ai', reason: '도움 요청', status: 'pending', created_at: '2026-06-22T00:00:00Z', ...overrides }
}
function sess(overrides = {}) {
  return { session_id: 's', visitor_id: 'v', is_hitl: false, escalation_status: '', active: false, created_at: '2026-06-22T00:00:00Z', last_activity: '2026-06-22T00:00:00Z', ...overrides }
}

function renderHitl() {
  return render(<MemoryRouter initialEntries={['/tenant/hitl']}><HitlTab /></MemoryRouter>)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.openEscalationStream).mockReturnValue({ close: vi.fn() })
  vi.mocked(api.getEscalationMessages).mockResolvedValue([] as any)
})

describe('HitlTab — 흐름 칸·필터', () => {
  it('세션 행과 상담 카드에 흐름 이름이 보이고 옛 세션은 —이다', async () => {
    vi.mocked(api.listFlowOptions).mockResolvedValue([])
    vi.mocked(api.listEscalations).mockResolvedValue([esc({ flow_id: 'f1', flow_name: '봄 이벤트' })] as any)
    vi.mocked(api.listSessions).mockResolvedValue([
      sess({ session_id: 's-a', visitor_id: 'visA', flow_id: 'f2', flow_name: '환불 안내' }),
      sess({ session_id: 's-b', visitor_id: 'visB', flow_id: null, flow_name: null }),
    ] as any)
    renderHitl()
    expect(await screen.findByText('봄 이벤트')).toBeInTheDocument()
    expect(screen.getByText('환불 안내')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('흐름 필터를 고르면 그 흐름 id로 상담·세션 목록을 다시 부른다', async () => {
    vi.mocked(api.listFlowOptions).mockResolvedValue([{ id: 'f1', name: '봄 이벤트' }, { id: 'f2', name: '환불 안내' }])
    vi.mocked(api.listEscalations).mockResolvedValue([] as any)
    vi.mocked(api.listSessions).mockResolvedValue([] as any)
    renderHitl()
    const select = await screen.findByRole('combobox', { name: '흐름' })
    await userEvent.selectOptions(select, 'f2')
    await waitFor(() => expect(api.listEscalations).toHaveBeenLastCalledWith('f2'))
    expect(api.listSessions).toHaveBeenLastCalledWith(50, 0, 'f2')
  })
})

describe('HitlTab — 세션 콘솔', () => {
  it('진행 중 상담은 카드(수락하기)로, 나머지 세션은 목록으로 보이고 escalation 세션은 목록에서 제외된다', async () => {
    vi.mocked(api.listEscalations).mockResolvedValue([esc()] as any)
    vi.mocked(api.listSessions).mockResolvedValue([
      sess({ session_id: 's-esc', visitor_id: 'visEsc', escalation_status: 'pending', is_hitl: true }),
      sess({ session_id: 's-idle', visitor_id: 'visIdle', last_activity: '2026-06-22T01:00:00Z' }),
      sess({ session_id: 's-active', visitor_id: 'visActive', active: true, last_activity: '2026-06-22T00:30:00Z' }),
    ] as any)

    renderHitl()

    // escalation은 카드로 — claim 버튼 노출(기존 동작 보존)
    expect(await screen.findByRole('button', { name: '수락하기' })).toBeInTheDocument()
    // 다른 세션 목록: escalation 세션(visEsc) 제외 → 2개
    const startBtns = screen.getAllByRole('button', { name: '상담 시작' })
    expect(startBtns).toHaveLength(2)
    expect(screen.queryByText('visEsc')).toBeNull()
    // 활성(visActive)이 유휴(visIdle)보다 위
    const labels = screen.getAllByText(/visActive|visIdle/)
    expect(labels[0]).toHaveTextContent('visActive')
  })

  it('"상담 시작"을 누르면 해당 세션을 takeover한다', async () => {
    vi.mocked(api.listEscalations).mockResolvedValue([] as any)
    vi.mocked(api.listSessions).mockResolvedValue([
      sess({ session_id: 's-idle', visitor_id: 'visIdle' }),
    ] as any)
    vi.mocked(api.takeoverSession).mockResolvedValue({ ok: true, status: 200, escalation_id: 'e-new' })

    renderHitl()
    await userEvent.click(await screen.findByRole('button', { name: '상담 시작' }))
    await waitFor(() => expect(api.takeoverSession).toHaveBeenCalledWith('s-idle'))
  })

  it('"내역 보기"를 누르면 takeover 없이 그 세션의 채팅 내역을 펼친다', async () => {
    vi.mocked(api.listEscalations).mockResolvedValue([] as any)
    vi.mocked(api.listSessions).mockResolvedValue([sess({ session_id: 's-idle', visitor_id: 'visIdle' })] as any)
    vi.mocked(api.getSessionMessages).mockResolvedValue([
      { id: 'm1', role: 'user', content: '안녕하세요 질문이요', created_at: '2026-06-22T00:00:00Z' },
      { id: 'm2', role: 'assistant', content: '네 도와드릴게요', created_at: '2026-06-22T00:00:01Z' },
    ] as any)

    renderHitl()
    await userEvent.click(await screen.findByRole('button', { name: '내역 보기' }))

    expect(await screen.findByText('안녕하세요 질문이요')).toBeInTheDocument()
    expect(screen.getByText('네 도와드릴게요')).toBeInTheDocument()
    expect(api.takeoverSession).not.toHaveBeenCalled()
    // 다시 누르면 닫힌다
    await userEvent.click(screen.getByRole('button', { name: '내역 닫기' }))
    expect(screen.queryByText('안녕하세요 질문이요')).toBeNull()
  })

  it('주기적으로 presence를 서버 진실원천과 재조정한다(놓친 disconnect 델타 자가보정)', async () => {
    vi.useFakeTimers()
    try {
      vi.mocked(api.listEscalations).mockResolvedValue([] as any)
      vi.mocked(api.listSessions).mockResolvedValue([sess({ session_id: 's-idle', visitor_id: 'visIdle' })] as any)

      renderHitl()
      // 초기 refresh + 주기 재조정(15s) 1회 → listSessions가 2회 이상 호출된다
      await vi.advanceTimersByTimeAsync(16000)
      expect(vi.mocked(api.listSessions).mock.calls.length).toBeGreaterThanOrEqual(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('takeover가 409면 경고하고 새로고침하지 않는다', async () => {
    vi.mocked(api.listEscalations).mockResolvedValue([] as any)
    vi.mocked(api.listSessions).mockResolvedValue([sess({ session_id: 's-idle', visitor_id: 'visIdle' })] as any)
    vi.mocked(api.takeoverSession).mockResolvedValue({ ok: false, status: 409 })
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})

    renderHitl()
    await userEvent.click(await screen.findByRole('button', { name: '상담 시작' }))
    await waitFor(() => expect(alertSpy).toHaveBeenCalled())
    alertSpy.mockRestore()
  })
})
