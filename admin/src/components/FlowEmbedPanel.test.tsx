import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import FlowEmbedPanel from './FlowEmbedPanel'
import { fakeFlowsApi } from '../flowsFakeApi'
import type { FlowOut } from '../generated/model'

const ORIGIN = 'https://flow.example.com'
const flow = (over: Partial<FlowOut> = {}): FlowOut => ({
  id: 'f1', name: '상담', is_public: false, embed_key: 'abcd1234', embed_enabled: false, embed_path: '/chatbot/acme/abcd1234/',
  deployed_version: 1, updated_at: '2026-10-01T09:00:00Z', has_pending_request: false, ...over,
})
const graph = { nodes: [], edges: [] }
const CODE = '<iframe src="https://flow.example.com/chatbot/acme/abcd1234/" width="400" height="600" style="border:none" allow="microphone"></iframe>'

let writeText: ReturnType<typeof vi.fn>
beforeEach(() => { writeText = vi.fn().mockResolvedValue(undefined); Object.assign(navigator, { clipboard: { writeText } }) })
afterEach(() => { vi.unstubAllGlobals() })

describe('FlowEmbedPanel — 임베드 메뉴', () => {
  it('Admin이 켜면 임베드 API를 부르고 바뀐 흐름을 알린다', async () => {
    const fake = fakeFlowsApi({ graph, flows: [flow()] })
    const onChange = vi.fn()
    render(<FlowEmbedPanel flow={flow()} deployed canManage origin={ORIGIN} onChange={onChange} />)
    await userEvent.click(screen.getByRole('checkbox', { name: '임베드 켜기' }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ embed_enabled: true })))
    expect(fake.calls.find((c) => c.path.endsWith('/flows/f1/embed/'))?.body).toEqual({ enabled: true })
  })

  it('배포 전이면 켤 수 없고 이유를 보인다', () => {
    render(<FlowEmbedPanel flow={flow({ deployed_version: null })} deployed={false} canManage origin={ORIGIN} onChange={vi.fn()} />)
    expect(screen.getByRole('checkbox', { name: '임베드 켜기' })).toBeDisabled()
    expect(screen.getByText(/배포한 버전이 없어 켤 수 없습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '기본 흐름으로' })).toBeDisabled()
  })

  it('서버가 거절하면 그 이유를 화면에 보인다', async () => {
    fakeFlowsApi({ graph, flows: [flow()], embedStatus: 409 })
    render(<FlowEmbedPanel flow={flow()} deployed canManage origin={ORIGIN} onChange={vi.fn()} />)
    await userEvent.click(screen.getByRole('checkbox', { name: '임베드 켜기' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('먼저 배포하세요')
  })

  it('켠 흐름은 코드를 보이고 Member도 복사·미리보기를 쓴다(켜고 끄기·기본 흐름은 못 한다)', async () => {
    render(<FlowEmbedPanel flow={flow({ embed_enabled: true })} deployed canManage={false} origin={ORIGIN} onChange={vi.fn()} />)
    expect(screen.getByTestId('flow-embed-code')).toHaveTextContent(CODE)
    await userEvent.click(screen.getByRole('button', { name: '코드 복사' }))
    expect(writeText).toHaveBeenCalledWith(CODE)
    expect(screen.getByRole('link', { name: /미리보기/ })).toHaveAttribute('href', `${ORIGIN}/chatbot/acme/abcd1234/`)
    expect(screen.getByRole('checkbox', { name: '임베드 켜기' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '기본 흐름으로' })).not.toBeInTheDocument()
  })

  it('꺼진 흐름에는 코드가 없고 켜라는 안내가 나온다', () => {
    render(<FlowEmbedPanel flow={flow()} deployed canManage origin={ORIGIN} onChange={vi.fn()} />)
    expect(screen.queryByTestId('flow-embed-code')).not.toBeInTheDocument()
    expect(screen.getByText(/임베드를 켜면 붙일 코드가 나타납니다/)).toBeInTheDocument()
  })

  it('「기본 흐름으로」를 누르면 기본 흐름 API를 부른다', async () => {
    const fake = fakeFlowsApi({ graph, flows: [flow()] })
    const onChange = vi.fn()
    render(<FlowEmbedPanel flow={flow()} deployed canManage origin={ORIGIN} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: '기본 흐름으로' }))
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ is_public: true })))
    expect(fake.calls.some((c) => c.method === 'POST' && c.path.endsWith('/flows/f1/default/'))).toBe(true)
  })

  it('기본 흐름은 임베드를 꺼도 열려 있고 코드를 보이며 「기본 흐름으로」가 없다', () => {
    render(<FlowEmbedPanel flow={flow({ is_public: true })} deployed canManage origin={ORIGIN} onChange={vi.fn()} />)
    expect(screen.getByText(/기본 흐름은 임베드를 꺼도 열려 있습니다/)).toBeInTheDocument()
    expect(screen.getByTestId('flow-embed-code')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '기본 흐름으로' })).not.toBeInTheDocument()
  })
})
