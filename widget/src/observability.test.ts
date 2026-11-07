// GlitchTip 설정(#308) — DSN 없으면 끔, 우리 번들 주소만 받기, 보내기 전 비밀 지우기.
// admin/src/observability.test.ts 와 같은 내용이다(파일이 두 앱에 복사돼 있다).
import { describe, expect, it } from 'vitest'
import type { ErrorEvent } from '@sentry/react'
import { buildSentryOptions, scriptBaseOf, scrubEvent } from './observability'
import runtimeConfigSource from '../public/runtime-config.js?raw'

const BASE = 'https://flow.example.com/embed/'
const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl'
const SHA = '0123456789abcdef0123456789abcdef01234567'

describe('buildSentryOptions', () => {
  it('DSN 이 없거나 비면 켜지 않는다', () => {
    expect(buildSentryOptions(undefined, BASE)).toBeNull()
    expect(buildSentryOptions({}, BASE)).toBeNull()
    expect(buildSentryOptions({ sentryDsn: '  ', sentryRelease: SHA }, BASE)).toBeNull()
  })

  it('운영 설정 — production, PII·추적 끔, release 는 실행 env 값', () => {
    const options = buildSentryOptions({ sentryDsn: 'http://public@127.0.0.1:9/1', sentryRelease: SHA }, BASE)
    expect(options).toMatchObject({
      dsn: 'http://public@127.0.0.1:9/1',
      release: SHA,
      environment: 'production',
      sendDefaultPii: false,
      tracesSampleRate: 0,
    })
    expect(buildSentryOptions({ sentryDsn: 'http://public@127.0.0.1:9/1', sentryRelease: '' }, BASE)?.release).toBeUndefined()
  })

  it('우리 번들 주소에서 난 오류만 받는다(allowUrls)', () => {
    const allow = buildSentryOptions({ sentryDsn: 'http://public@127.0.0.1:9/1' }, BASE)?.allowUrls ?? []
    const allowed = (url: string) => allow.some((rule) => (typeof rule === 'string' ? url.includes(rule) : rule.test(url)))
    expect(allowed(`${BASE}assets/index-abc.js`)).toBe(true)
    expect(allowed('https://customer.example/app.js')).toBe(false)
    expect(allowed('chrome-extension://abc/content.js')).toBe(false)
    expect(allowed('https://flow.example.com/other/x.js')).toBe(false)
    // 출처가 접두어로만 같은 남의 호스트(flow.example.com.evil.io)는 막는다
    expect(allowed('https://flow.example.com.evil.io/embed/x.js')).toBe(false)
  })

  it('beforeSend 가 비밀을 지운다', () => {
    const options = buildSentryOptions({ sentryDsn: 'http://public@127.0.0.1:9/1' }, BASE)
    const event = {
      type: undefined,
      message: `failed Bearer ${JWT}`,
      exception: { values: [{ type: 'Error', value: `token ${JWT} rejected` }] },
      breadcrumbs: [{ message: 'GET /api/x?access_token=abc123&page=2' }],
      request: { url: `${BASE}?token=zzz`, cookies: { a: 'b' }, headers: { Authorization: 'Bearer x', Accept: 'json' } },
    } as ErrorEvent
    const sent = options?.beforeSend?.(event, {}) as ErrorEvent
    const text = JSON.stringify(sent)
    for (const secret of [JWT, 'abc123', 'zzz', 'Bearer x']) expect(text).not.toContain(secret)
    expect(text).toContain('page=2')
    expect(sent.request?.cookies).toBeUndefined()
    expect(sent.request?.headers).toEqual({ Accept: 'json' })
  })
})

describe('scrubEvent', () => {
  it('sk- 키와 password= 값을 가린다', () => {
    const out = scrubEvent({ type: undefined, extra: { k: 'sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX', u: 'x?password=p1' } } as ErrorEvent)
    expect(JSON.stringify(out)).not.toMatch(/ABCDEFGHIJKLMNOP|p1/)
  })
})

describe('scriptBaseOf', () => {
  it('출처와 Vite base 로 번들 주소를 만든다', () => {
    expect(scriptBaseOf('https://flow.example.com', '/embed/')).toBe(BASE)
  })
})

describe('runtime-config.js', () => {
  it('저장소 기본값은 빈 설정이다 — dev·시험·e2e 에서 SDK 가 꺼진다', () => {
    const win: Pick<Window, '__FLOW_RUNTIME__'> = {}
    new Function('window', runtimeConfigSource)(win)
    expect(win.__FLOW_RUNTIME__).toEqual({})
    expect(buildSentryOptions(win.__FLOW_RUNTIME__, BASE)).toBeNull()
  })
})
