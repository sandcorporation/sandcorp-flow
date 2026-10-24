# ADR-0026: Embed Chat을 「Sandcorp Flow」 SaaS로 전환

## Status
Accepted (2026-10-24, 사용자 결정)

## Context
Embed Chat(GraphRAG 멀티테넌트 챗봇, LangGraph·HITL·위젯·Admin UI)은 운영된 적이 없어 옮길 데이터가 없다.
챗봇 하나의 고정 그래프(검색 → LLM → 필요 시 사람 넘김)로는 고객사마다 다른 상담 절차(의도 분류, 되묻기,
사내 API 조회, 금지 표현 차단)를 담을 수 없다. 이를 「테넌트가 흐름을 그려 배포하는」 제품으로 넓힌다.

## Decision
- **상품**: 「Sandcorp Flow」, 주소 `flow.sandcorp.co.kr`. 처음부터 **셀프 가입 SaaS**로 내며 누구나 테넌트를 만든다.
  **무료 베타**로 시작하고, 결제(토스페이먼츠 구독)는 다음 판이다. 요금표는 「준비 중」으로 둔다.
- **엔진은 Embed Chat을 확장한다**(새로 쓰지 않는다). 지식 검색(GraphRAG), 체크포인트, HITL, SSE, 위젯, Provider 설정을 재사용한다.
  고정 챗 그래프는 테넌트가 편집하는 **흐름(Flow)** 으로 바뀐다(ADR-0027).
- **LLM 비용은 고객 키(BYOK)**: 기존 테넌트별 Provider(ADR-0012)를 그대로 쓴다. 가입 직후에는 **플랫폼 키**로 도는
  **체험 한도**(예: 대화 200턴)를 준다. 한도를 넘으면 테넌트가 자기 키를 넣어야 흐름이 돈다.
- **운영 박스**: 기존 Oracle A1(4코어, 남은 메모리 약 8.7G, 이미 컨테이너 55개). 운영 compose에는 Ollama·PaddleOCR을
  넣지 않는다(운영은 테넌트 Provider). Jenkins로 배포하며, 실제 배포·서버 설정은 저장소 밖에서 한다.
- **인증은 sandcorp-auth로 통합**한다(ADR-0028). 고객 가입·테넌트 생성·체험 한도는 sandcorp-auth 다중 사용자화에 막혀 있다.
- **비로그인 체험 `/try`**: 예제 흐름 사본을 브라우저 세션에 만들어 편집·시험 대화만 한다(배포 불가, 24시간 뒤 삭제).
  플랫폼 키(Haiku급), 세션당 20턴, IP당 하루 3세션, 전체 하루 비용 $5 상한, 가짜 API·정해 둔 지식 묶음만. 「이 흐름으로 가입」으로 새 테넌트에 옮긴다.
- **편집기는 기존 Admin UI에 붙인다**(React·Tailwind, ADR-0017 유지)와 `@xyflow/react`(MIT). Admin UI 전체에 산업용 HMI
  시안(`docs/design/sandcorp-agents.html`, `docs/design/agents-industrial.css`)의 색과 모양을 입힌다.

## Considered Options
- **새 저장소로 처음부터**: 기각. 지식 검색·HITL·SSE·Provider·체크포인트가 이미 시험으로 덮여 있다.
- **처음엔 대표 한 사람의 내부 도구로**: 기각. 사용자가 SaaS로 바로 내기로 했다.
- **플랫폼이 LLM 비용을 흡수하고 요금으로 회수**: 기각. 결제가 다음 판이라 회수 수단이 없다. 체험 한도만 플랫폼이 낸다.

## Consequences
- 이름(README, compose 프로젝트, 패키지, 이미지)을 Sandcorp Flow로 바꾼다.
- 체험 한도와 `/try` 비용 상한 때문에 **턴·토큰·비용 계량**이 필요하다(지금은 토큰 일 롤업만 있고 비용 금액·한도 강제는 없다).
- 공개 가입이라 남용 방어(가입·체험 레이트리밋)가 계속 필요하다.
