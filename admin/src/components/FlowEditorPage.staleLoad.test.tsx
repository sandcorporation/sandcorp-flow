// #167: 개발 서버(StrictMode)는 첫 불러오기를 두 번 보낸다. 늦게 온 응답이 그 사이에 더한 노드를 지우면 안 된다.
import { StrictMode } from 'react'
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import * as api from '../api'
import FlowEditorPage from './FlowEditorPage'
import { fakeFlowsApi } from '../flowsFakeApi'
import { flushAsync } from '../flushAsync'

vi.mock('../api', () => ({ currentAgentRole: vi.fn() }))

beforeEach(() => { vi.mocked(api.currentAgentRole).mockReturnValue('admin') })
afterEach(() => { vi.unstubAllGlobals() })

describe('편집기 첫 불러오기 경합(#167)', () => {
  it('늦게 도착한 흐름 응답이 그 사이에 더한 노드를 지우지 않는다', async () => {
    fakeFlowsApi({ graph: { nodes: [], edges: [] } })
    const real = globalThis.fetch
    const held: (() => void)[] = []
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const isFlowGet = /\/api\/tenant\/flows\/f1\/$/.test(String(input)) && (init?.method ?? 'GET').toUpperCase() === 'GET'
      if (!isFlowGet) return real(input, init)
      return new Promise<Response>((resolve) => held.push(() => resolve(real(input, init))))
    })
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/tenant/flows/f1']}>
          <Routes><Route path="/tenant/flows/:id" element={<FlowEditorPage />} /></Routes>
        </MemoryRouter>
      </StrictMode>,
    )
    await waitFor(() => expect(held.length).toBeGreaterThanOrEqual(2))
    await act(async () => { held[0]() })
    await flushAsync()
    const early = screen.queryByRole('button', { name: '시작' })
    if (early) await userEvent.click(early)
    await act(async () => { held.slice(1).forEach((h) => h()) })
    const start = screen.queryByRole('button', { name: '시작' }) ?? (await screen.findByRole('button', { name: '시작' }))
    if (!early) await userEvent.click(start)
    await waitFor(() => expect(document.querySelectorAll('.react-flow__node').length).toBe(1))
    await flushAsync()
    expect(document.querySelectorAll('.react-flow__node').length).toBe(1)
  })
})
