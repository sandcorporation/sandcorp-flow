// 시험 대화 노드 이벤트(#239) — 실제 스택(uvicorn api-e2e + fake-llm)에서 SSE가 턴 도중에 차례대로·실시간으로 온다.
// 화면은 T2(#240) 몫이고 여기서는 네트워크 수준만 고정한다. fake-llm 마커: [[delay:N]] N초 지연, [[fail]] 400 오류.
const { test, expect } = require('@playwright/test')
const { provisionTenant } = require('./memberToken')

const API_URL = process.env.API_URL || 'http://localhost:8000'

const GRAPH = {
  nodes: [
    { id: 'start', kind: 'start', code: 'N-01', name: '시작', position: { x: 0, y: 40 }, config: {} },
    { id: 'answer', kind: 'ai_answer', code: 'N-02', name: '답변', position: { x: 260, y: 40 }, config: { instructions: '짧게 답한다.' } },
  ],
  edges: [{ from: 'start', to: 'answer' }],
}

async function api(token, method, path, body) {
  return fetch(`${API_URL}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
}

// SSE를 읽으며 이벤트마다 도착 시각(ms)을 붙인다 — 응답을 다 받은 뒤가 아니라 청크가 올 때마다.
async function readEvents(response) {
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const events = []
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let cut
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      if (block.startsWith(':')) continue // SSE 주석 하트비트(#253)
      const lines = Object.fromEntries(block.split('\n').map((l) => [l.slice(0, l.indexOf(': ')), l.slice(l.indexOf(': ') + 2)]))
      events.push({ at: Date.now(), name: lines.event, data: JSON.parse(lines.data) })
    }
  }
  return events
}

async function newFlow(token) {
  const made = await api(token, 'POST', '/api/tenant/flows/', { name: `노드 이벤트 ${Date.now()}`, graph: GRAPH })
  expect(made.ok).toBeTruthy()
  return (await made.json()).id
}

test.describe('시험 대화 SSE 노드 이벤트', () => {
  test('느린 LLM에서도 노드 이벤트가 차례대로, 턴이 끝나기 전에 온다', async () => {
    const tenant = await provisionTenant(`Node Events ${Date.now()}`)
    const id = await newFlow(tenant.token)

    const response = await api(tenant.token, 'POST', `/api/tenant/flows/${id}/test/`, { message: '안녕하세요 [[delay:1]]' })
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toContain('text/event-stream')
    const events = await readEvents(response)

    expect(events.map((e) => e.name)).toEqual([
      'start', 'node_start', 'node_end', 'node_start', 'node_end', 'trace', 'trace', 'reply', 'done',
    ])
    expect(events.filter((e) => e.name === 'node_start').map((e) => [e.data.node_id, e.data.step])).toEqual([['start', 0], ['answer', 1]])
    const answerStart = events.find((e) => e.name === 'node_start' && e.data.node_id === 'answer')
    const answerEnd = events.find((e) => e.name === 'node_end' && e.data.node_id === 'answer')
    // 답 노드 시작 이벤트가 LLM 지연(1초)만큼 먼저 도착했다 — 모았다 한꺼번에 보내지 않는다
    expect(answerEnd.at - answerStart.at).toBeGreaterThanOrEqual(800)
    expect(Object.keys(answerEnd.data).sort()).toEqual([
      'cost_usd', 'duration_ms', 'handle', 'input_tokens', 'kind', 'node_id', 'output_tokens', 'step', 'summary', 'unpriced_models',
    ])
    expect(answerEnd.data.summary).toMatch(/^답 \d+자$/)
    expect(answerEnd.data.duration_ms).toBeGreaterThanOrEqual(800)
    expect(events.at(-1).data.ran).toBe(true)
  })

  test('지식 검색 노드의 node_start는 검색이 끝나기 전에 온다(#256)', async () => {
    // 실제 uvicorn(요청마다 thread-sensitive 스레드 하나) + 실제 HTTP 임베딩 수신부(fake-llm /v1/embeddings, #260).
    // 가짜는 즉시 답하므로 질의의 `[[delay:0.6]]` 마커로 임베딩 응답을 0.6초 늦춘다(옛 ollama 긴 질의 느림의 대체).
    // 마커는 답 노드 LLM 호출도 같이 늦춘다 — 3초 한도 안에 두려고 1초에서 줄였다(#257). 고치기 전 간격은 0ms였으니 0.3초 이상이면 가른다.
    // 고치기 전에는 kb 검색이 요청의 sync 스레드를 차지해 스트림의 이벤트 가림이 그 뒤에 줄 섰고,
    // kb node_start가 node_end와 같은 ms에 왔다(운영 측정 duration_ms 2810, 도착 5290/5290).
    const tenant = await provisionTenant(`Node Events KB ${Date.now()}`)
    const graph = {
      nodes: [
        GRAPH.nodes[0],
        { id: 'kb', kind: 'knowledge_search', code: 'N-03', name: '지식 검색', position: { x: 130, y: 40 }, config: {} },
        GRAPH.nodes[1],
      ],
      edges: [{ from: 'start', to: 'kb' }, { from: 'kb', to: 'answer' }],
    }
    const made = await api(tenant.token, 'POST', '/api/tenant/flows/', { name: `kb 이벤트 ${Date.now()}`, graph })
    expect(made.ok).toBeTruthy()
    const id = (await made.json()).id
    const message = '환불 규정과 교환 절차, 배송 기간을 자세히 알려 주세요. [[delay:0.6]]'

    const events = await readEvents(await api(tenant.token, 'POST', `/api/tenant/flows/${id}/test/`, { message }))

    const kbStart = events.find((e) => e.name === 'node_start' && e.data.node_id === 'kb')
    const kbEnd = events.find((e) => e.name === 'node_end' && e.data.node_id === 'kb')
    expect(kbEnd.data.duration_ms).toBeGreaterThan(0)
    // 시작 이벤트가 검색 시간만큼 먼저 왔다(도착 시각 잡음 300ms 여유)
    expect(kbEnd.at - kbStart.at).toBeGreaterThanOrEqual(kbEnd.data.duration_ms - 300)
    expect(events.at(-1).data.ran).toBe(true)
  })

  test('LLM 오류면 node_error 뒤 done(turn_error)이 온다', async () => {
    const tenant = await provisionTenant(`Node Error ${Date.now()}`)
    const id = await newFlow(tenant.token)

    const response = await api(tenant.token, 'POST', `/api/tenant/flows/${id}/test/`, { message: '안녕하세요 [[fail]]' })
    const events = await readEvents(response)

    expect(events.map((e) => e.name)).toEqual(['start', 'node_start', 'node_end', 'node_start', 'node_error', 'done'])
    const error = events.find((e) => e.name === 'node_error').data
    expect(error.node_id).toBe('answer')
    expect(error.message).toContain('오류')
    expect(events.at(-1).data).toMatchObject({ ran: false, code: 'turn_error' })
  })
})
