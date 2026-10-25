import { describe, it, expect, vi, afterEach } from 'vitest'
import { streamTestTurn } from './testStream'
import { setAccess } from '../auth'

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })

describe('streamTestTurn — 체험 경로(#37)', () => {
  it('base 가 /api/try 면 그 경로로 쿠키만 실어 보내고 done.code 를 그대로 넘긴다', async () => {
    setAccess('agent', 'ag-tok')
    const text = 'event: done\ndata: {"ran":false,"note":"내일","errors":[],"code":"try_daily_budget"}\n\n'
    const fetchMock = vi.fn().mockResolvedValue(new Response(text, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const seen: unknown[] = []

    await streamTestTurn('f1', '안녕', null, (e) => seen.push(e), undefined, '/api/try')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/try/flows/f1/test/')
    expect(new Headers(init.headers).get('Authorization')).toBeNull()
    expect(init.credentials).toBe('include')
    expect(seen).toEqual([{ event: 'done', data: { ran: false, note: '내일', errors: [], code: 'try_daily_budget' } }])
  })
})
