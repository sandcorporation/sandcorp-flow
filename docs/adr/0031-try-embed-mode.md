# ADR-0031: /try는 임베드 모드(`?embed=1`)로 같은 사이트 portfolio iframe에 들어간다

## Status
Accepted (2026-10-25, 사용자·대장 결정 — #178·#179·#180·#181)

## Context
portfolio(sandcorp.co.kr)가 홈 화면 안에서 「고쳐 보기」 체험을 바로 보여 주고 싶다. 체험(Try Session, ADR-0030)은 쿠키 세션이고, 쓰기는
`SameSite=Strict` 쿠키 + Origin 검사로 CSRF를 막는다. iframe으로 넣으려면 프레임 허용·화면(머리줄·높이)·쿠키·가입 흐름을 정해야 한다.

## Decision
- **같은 사이트 전제 — 쿠키는 Strict를 유지한다.** 부모(`sandcorp.co.kr`)와 flow(`flow.sandcorp.co.kr`)는 eTLD+1이 같아 iframe 안 요청에도 Strict 쿠키가 간다.
  쿠키를 `None`으로 풀면 모든 사이트의 임베드·CSRF 표면이 열리므로 풀지 않는다. 교차 사이트(고객 사이트) 임베드는 **범위 밖**이다 — 필요해지면 쿠키 전략(CHIPS/`None`)·Origin 검사를 그때 다시 정한다.
- **프레임 허용은 경로별 CSP `frame-ancestors`만, 브라우저가 강제한다.** `/admin-ui/try…` = `'self'` + `TRY_EMBED_ORIGINS`(공백 구분 목록; 운영 `https://sandcorp.co.kr https://www.sandcorp.co.kr`),
  나머지 admin·랜딩 = `'self'`, 위젯 `/chatbot/`·`/embed/` = `*`. 앱은 `X-Frame-Options`를 내지 않고, NPMplus의 XFO는 upstream에서 뺀다(#179). `document.referrer`로 부모를 정하지 않는다(portfolio가 `Referrer-Policy: same-origin`).
- **임베드 판정은 `?embed=1` 쿼리 → 메모리 + sessionStorage.** SPA 이동·새로고침에도 유지되고, `noopener`로 여는 새 탭엔 따라가지 않아 새 탭은 일반 화면이다.
- **높이는 hello 규약.** 부모가 `{type:'sandcorp-flow:hello'}`를 보내면 iframe이 `event.source === window.parent`일 때만 `event.origin`으로 `{type:'sandcorp-flow:height', height}`를 보낸다.
  허용 출처 목록을 SPA에 내려보내지 않으려는 선택이다(목록은 nginx `frame-ancestors`에만 있다). iframe이 떴다는 것은 브라우저가 조상 전부를 허용했다는 뜻이고, 보내는 것은 높이 숫자뿐이다. `'*'`는 쓰지 않는다. 임베드에선 뷰포트 단위를 쓰지 않아(폰 캔버스 480px·넓은 폭 격자 640px 고정) 호스트가 iframe을 키워도 순환하지 않는다.
- **바깥 링크·「이 흐름으로 가입」은 새 탭**(`target=_blank rel=noopener`). 가입은 sandcorp-auth 최상위 이동이 필요하고(로그인 화면을 iframe 안에서 열지 않는다), 새 탭은 같은 사이트라 같은 체험 쿠키로 같은 세션을 옮긴다.
- **자동 세션 생성 금지.** `template=home`이어도 홈 카드가 골라진 첫 화면에서 「고쳐 보기 시작」을 한 번 눌러야 세션이 생긴다. 방문마다 세션을 만들면 봇·새로고침이 IP 하루 3세션과 전체 하루 $5 한도를 쓴다(대장 결정).
  - (#195 이후) 진입은 여전히 세션을 만들지 않지만 「고쳐 보기 시작」 버튼은 없어졌다 — `template=home`이면 곧바로 홈 편집기가 열리고 세션은 첫 쓰기(위치 이동만 빼고 초안 저장·시험 대화)에 생긴다. ADR-0032(초안, #194)가 #200에서 이 문장을 대신한다.

## Consequences
- portfolio는 이 ADR의 규약(`docs/deployment.md` 「체험 임베드」)대로 iframe + 부모 스크립트를 붙이면 된다. 출처 추가는 flow 쪽 `TRY_EMBED_ORIGINS` 변경 + nginx 재생성.
- 방문 즉시 편집기가 열리게 하려면(세션 지연 생성) 한도 정책을 다시 정해야 한다 — 사용자 선택지로 보고됨, 이 ADR의 범위 밖.
- 시험: e2e는 같은 사이트 두 호스트(`portfolio.flowtest.test`/`flow.flowtest.test`)로 iframe 여정을 확인한다(`try-embed-hosts.spec.js`). e2e 스택은 Vite라 `frame-ancestors`가 없어 **다른 사이트 거부는 nginx 계약 시험**(`scripts/test-nginx-metrics.sh`: 경로별 CSP 값)이 맡는다.
