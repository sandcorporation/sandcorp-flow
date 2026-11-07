// 운영 오류 수집(GlitchTip, #308) — SDK 는 @sentry/react, 받는 쪽은 우리 GlitchTip.
//
// **이 파일은 admin·widget 에 같은 내용으로 복사돼 있다**(`admin/src/observability.ts`·
// `widget/src/observability.ts`). 두 앱은 따로 빌드되는 패키지라 공유 모듈이 없다 — 고치면 둘 다 고친다.
//
// **DSN·release 는 빌드 때 박지 않는다.** 이미지 하나를 운영에 쓰므로, 운영 init 컨테이너가 시작할 때
// 실행 env(SENTRY_DSN·SENTRY_RELEASE)로 `runtime-config.js` 를 다시 쓰고(`runtime-config.sh`) index.html 이
// 앱보다 먼저 읽는다. 저장소의 `public/runtime-config.js` 는 빈 값이라 dev·시험·e2e 에서는 SDK 가 꺼진다.
//
// **우리 번들에서 난 오류만 보낸다**(`allowUrls`). 두 앱은 우리 출처의 문서 안에서만 돈다 — widget 은
// 고객 사이트에 `<iframe src=".../chatbot/<slug>/">` 로 들어가고(스크립트를 고객 페이지에 심지 않는다),
// admin 의 /try 도 iframe 이다. 그래서 전역 onerror·unhandledrejection 은 iframe 안 우리 window 만 감싸고,
// 고객 window 의 전역 Sentry 와는 다른 전역이다. 그래도 같은 문서에 끼어드는 브라우저 확장 스크립트의
// 오류가 들어오지 않게 프레임 주소가 우리 base(`/embed/`·`/admin-ui/`) 아래인 것만 받는다.
//
// **세션 리플레이·성능 추적은 켜지 않는다.** 오류만 보낸다. 자격증명·토큰 모양 문자열은 `beforeSend`
// 에서 지운다(`sendDefaultPii: false` 하나를 믿지 않는다 — 백엔드 `apps.core.observability` 와 같은 원칙).

import type { BrowserOptions, ErrorEvent } from '@sentry/react'

/** 운영 init 컨테이너가 쓰는 `window.__FLOW_RUNTIME__` 의 모양. */
export type FlowRuntimeConfig = {
  sentryDsn?: string
  sentryRelease?: string
}

declare global {
  interface Window {
    __FLOW_RUNTIME__?: FlowRuntimeConfig
  }
}

const FILTERED = '[Filtered]'

/** 이름으로 지우는 요청 헤더(소문자). 백엔드 `_CREDENTIAL_HEADERS` 와 같은 목록. */
const CREDENTIAL_HEADERS = new Set([
  'cookie',
  'set-cookie',
  'authorization',
  'proxy-authorization',
  'x-api-key',
  'x-internal-token',
])

/** 토큰 모양 — 백엔드 `_TOKEN_PATTERNS` 와 같은 규칙. */
const TOKEN_PATTERNS: ReadonlyArray<[RegExp, string]> = [
  [/eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*/g, FILTERED],
  [/\b(bearer|token|basic)\s+[A-Za-z0-9._~+/=-]{8,}/gi, `$1 ${FILTERED}`],
  [/\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}/g, FILTERED],
  [/\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{16,}/g, FILTERED],
  [
    /\b((?:access_|refresh_|id_)?token|api_?key|secret|password|passwd|code|signature)=[^&\s"']+/gi,
    `$1=${FILTERED}`,
  ],
]

export function scrubString(value: string): string {
  return TOKEN_PATTERNS.reduce((text, [pattern, replacement]) => text.replace(pattern, replacement), value)
}

function scrubValue(value: unknown): unknown {
  if (typeof value === 'string') return scrubString(value)
  if (Array.isArray(value)) return value.map(scrubValue)
  if (value !== null && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value)) out[key] = scrubValue(item)
    return out
  }
  return value
}

/** 쿠키·요청 본문·자격증명 헤더를 지우고, 남은 모든 문자열에서 토큰 모양을 가린다. */
export function scrubEvent(event: ErrorEvent): ErrorEvent {
  const request = event.request
  if (request) {
    delete request.cookies
    delete request.data
    const headers = request.headers
    if (headers) {
      for (const name of Object.keys(headers)) {
        if (CREDENTIAL_HEADERS.has(name.toLowerCase())) delete headers[name]
      }
    }
  }
  // 문자열만 바꾸고 모양은 그대로 둔다 — 같은 키를 가진 객체라 ErrorEvent 로 돌려준다.
  return scrubValue(event) as ErrorEvent
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * `Sentry.init` 설정. DSN 이 비면 null — 그러면 켜지 않는다.
 * `scriptBase` 는 우리 번들이 놓인 주소(예: `https://flow.sandcorp.co.kr/embed/`)로, 그 아래 프레임에서 난 오류만 받는다.
 */
export function buildSentryOptions(config: FlowRuntimeConfig | undefined, scriptBase: string): BrowserOptions | null {
  const dsn = (config?.sentryDsn ?? '').trim()
  if (dsn === '') return null
  const release = (config?.sentryRelease ?? '').trim()
  return {
    dsn,
    release: release === '' ? undefined : release,
    environment: 'production',
    sendDefaultPii: false,
    tracesSampleRate: 0,
    allowUrls: [new RegExp(`^${escapeRegExp(scriptBase)}`)],
    beforeSend: (event) => scrubEvent(event),
  }
}

/** 우리 번들의 주소 — 같은 출처 + Vite base(`/embed/`·`/admin-ui/`). */
export function scriptBaseOf(origin: string, baseUrl: string): string {
  return new URL(baseUrl, origin).href
}
