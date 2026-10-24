import { describe, it, expect } from 'vitest'
import { urlHost, hostAllowed, secretNamesUsed } from './httpToolWarn'

describe('urlHost — https 주소의 호스트(포트·경로 제외, 소문자)', () => {
  it.each([
    ['https://api.example.com/orders?a=1', 'api.example.com'],
    ['https://API.Example.com:8443/x', 'api.example.com'],
    ['https://api.example.com./x', 'api.example.com'],
    ['https://api.example.com', 'api.example.com'],
    ['https://api.example.com/{{state.fields.id}}', 'api.example.com'],
  ])('%s → %s', (url, host) => expect(urlHost(url)).toBe(host))

  it.each(['', 'http://api.example.com', 'api.example.com', 'https://', 'https://{{state.fields.h}}.example.com/x', 'https://u@x.com/'])
  ('호스트를 알 수 없으면 null: %j', (url) => expect(urlHost(url)).toBeNull())
})

describe('hostAllowed — 서버 AllowList 와 같은 규칙', () => {
  const patterns = ['api.example.com', '*.shop.io']
  it.each([
    ['api.example.com', true, '정확 일치'],
    ['API.example.com', true, '대소문자 무시'],
    ['www.example.com', false, '다른 하위 도메인'],
    ['example.com', false, 'apex'],
    ['x.api.example.com', false, '정확 일치는 하위를 포함하지 않는다'],
    ['a.shop.io', true, '와일드카드 한 단계 하위'],
    ['shop.io', false, '와일드카드는 apex 제외'],
    ['a.b.shop.io', false, '두 단계 이상 아님'],
    ['evilshop.io', false, '점 없이 이어 붙인 이름'],
  ])('%s → %s (%s)', (host, ok) => expect(hostAllowed(host, patterns)).toBe(ok))

  it('목록이 비면 아무것도 허용하지 않는다', () => expect(hostAllowed('api.example.com', [])).toBe(false))
  it('패턴 표기의 대소문자·끝 점을 정리해 비교한다', () => expect(hostAllowed('a.shop.io', ['*.Shop.io.'])).toBe(true))
})

describe('secretNamesUsed — config 에서 쓰는 {{secret.이름}}', () => {
  it('헤더 값과 본문 틀의 참조를 중복 없이 모은다', () => {
    expect(secretNamesUsed({ headers: { Authorization: 'Bearer {{secret.shop_key}}', 'X-A': '{{ secret.other }}' }, body_template: '{"k":"{{secret.shop_key}}"}' }).sort())
      .toEqual(['other', 'shop_key'])
  })
  it('없으면 빈 배열', () => expect(secretNamesUsed({ url: 'https://a.com' })).toEqual([]))
})
