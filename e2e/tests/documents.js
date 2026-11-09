const { expect } = require('@playwright/test')

/**
 * 업로드한 문서의 색인이 ready 가 될 때까지 목록 API 로 다시 본다(#339).
 * 색인은 서버 비동기 작업이라 알릴 사건이 없다 — 상태를 다시 본다.
 * failed 면 받은 값에 error_message 를 붙여 단언 메시지에 실패 이유가 보이게 한다.
 */
async function expectDocumentReady(api, token, docId, { timeout = 30_000, intervals = [50, 100, 200, 400] } = {}) {
  await expect.poll(async () => {
    const docs = await (await api.get('/api/tenant/documents/', { headers: { Authorization: `Bearer ${token}` } })).json()
    const doc = docs.find((d) => d.id === docId)
    if (!doc) return 'missing'
    return doc.status === 'failed' ? `failed: ${doc.error_message}` : doc.status
  }, { timeout, intervals, message: `문서 ${docId} 색인이 ready 가 되어야 한다` }).toBe('ready')
}

module.exports = { expectDocumentReady }
