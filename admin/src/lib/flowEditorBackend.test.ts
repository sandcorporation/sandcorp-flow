import { describe, it, expect } from 'vitest'
import { tryBackend, tryViewBackend, publicViewBackend } from './flowEditorBackend'

// 체험·공개 보기에는 테넌트 API(허용 도메인·비밀)가 없다 — 불러 봐야 401(#150). 주입 자리에서 끈다.
describe('체험 백엔드는 테넌트 조회를 건너뛴다', () => {
  it('tryBackend·tryViewBackend 는 tenantLookups=false', () => {
    expect(tryBackend.tenantLookups).toBe(false)
    expect(tryViewBackend.tenantLookups).toBe(false)
  })
  it('공개 보기도 tenantLookups=false', () => {
    const b = publicViewBackend({ slug: 's', flow_name: 'n', version: 1, graph: {} } as never)
    expect(b.tenantLookups).toBe(false)
  })
})
