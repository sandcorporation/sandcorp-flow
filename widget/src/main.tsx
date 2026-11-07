import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { buildSentryOptions, scriptBaseOf } from './observability'

// 운영 오류 수집(GlitchTip, #308). DSN 은 실행 설정(runtime-config.js)에서 온다 — 비면 SDK 를 받지도 않는다.
const sentryOptions = buildSentryOptions(window.__FLOW_RUNTIME__, scriptBaseOf(window.location.origin, import.meta.env.BASE_URL))
if (sentryOptions) void import('@sentry/react').then((Sentry) => Sentry.init(sentryOptions))

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
