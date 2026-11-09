// 정보통신망법·전자상거래법 사업자 정보(법적 표시 의무). 변경 시 정확히 갱신할 것.
// 통신판매업신고번호는 유료 통신판매 개시 시 신고 후 추가한다.
// 전화번호는 코드에 두지 않는다(#326) — 운영 env LANDING_TEL 이 runtime-config.js 로 들어온다.
export function landingTel(): string {
  return window.__FLOW_RUNTIME__?.landingTel?.trim() ?? ''
}

export const BUSINESS = {
  name: '샌드코프',
  ceo: '김한얼',
  bizNo: '752-26-01740',
  salesNo: '',                       // 통신판매업신고번호(예: 제2026-서울동작-0000호) — 신고 후 입력
  address: '서울특별시 여의대방로24나길 15, 102호',
  email: 'gksdjf1690@gmail.com',
  privacyOfficer: '김한얼',
}
