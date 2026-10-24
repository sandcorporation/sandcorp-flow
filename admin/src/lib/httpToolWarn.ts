// HTTP 도구 편집기의 경고용 순수 규칙. 최종 판정은 서버(저장 검증·실행 때 AllowList)가 하고, 여기는 미리 알려 주기만 한다.
import { SECRET_REF_RE } from './nodeFormLabels'
import type { JsonObject } from './flowGraph'

const norm = (s: string) => s.trim().toLowerCase().replace(/\.$/, '')

/** https 주소의 호스트(소문자, 포트·끝 점 제외). 호스트를 알 수 없으면(http·참조·userinfo·빈 값) null. */
export function urlHost(url: string): string | null {
  const m = /^https:\/\/([^/?#]*)/i.exec(url.trim())
  if (!m || m[1] === '' || m[1].includes('@') || m[1].includes('{{')) return null
  const host = norm(m[1].replace(/:\d*$/, ''))
  return host === '' ? null : host
}

/** 서버 safe_http.AllowList 와 같은 규칙: 정확 일치, 또는 `*.base` 의 한 단계 하위(apex·두 단계 이상은 아님). */
export function hostAllowed(host: string, patterns: readonly string[]): boolean {
  const h = norm(host)
  const exact = new Set<string>()
  const wild = new Set<string>()
  for (const raw of patterns) {
    const p = norm(raw)
    if (p.startsWith('*.')) wild.add(p.slice(2))
    else exact.add(p)
  }
  if (exact.has(h)) return true
  const i = h.indexOf('.')
  return i > 0 && wild.has(h.slice(i + 1))
}

/** config 의 헤더 값·본문 틀에서 쓰는 {{secret.이름}} 의 이름들(중복 없이, 나타난 순서). */
export function secretNamesUsed(config: JsonObject): string[] {
  const texts: string[] = []
  const headers = config.headers
  if (headers && typeof headers === 'object') for (const v of Object.values(headers)) if (typeof v === 'string') texts.push(v)
  if (typeof config.body_template === 'string') texts.push(config.body_template)
  const names = new Set<string>()
  for (const t of texts) for (const m of t.matchAll(new RegExp(SECRET_REF_RE.source, 'g'))) names.add(m[1])
  return [...names]
}
