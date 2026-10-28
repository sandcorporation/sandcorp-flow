# Domain Glossary — Sandcorp Flow

Sandcorp Flow(`flow.sandcorp.co.kr`)는 옛 Embed Chat을 넓힌 셀프 가입 SaaS다. 테넌트가 상담 **흐름(Flow)** 을 그려 시험·배포하고, 위젯에서 방문자가 그 흐름과 대화한다(ADR-0026). 아래 「Flow」 절이 새 용어이고, 나머지 절은 재사용하는 엔진의 용어다.

## Actors

**Operator**
플랫폼 자체를 운영하는 쪽(대표). 어드민 페이지 최상위 권한 보유. Tenant 정지, 전체 현황 모니터링 담당. **sandcorp-auth에서 받은 대표 토큰의 Flow 리소스 `admin` 스코프가 곧 Operator 권한**이다(ADR-0028). Flow 안에 Operator 비밀번호는 두지 않는다 — 관리 화면 로그인은 sandcorp-auth 기밀 클라이언트 콜백뿐이고, 대표(A1) + `admin`일 때만 Flow 세션(Access + Refresh Family)을 연다(#21). Flow 쪽 Operator 행은 **사용자**(`User`, External Identity `(iss, sub)`)에 1:1로 이어진다.

**Tenant**
챗봇을 자기 사이트에 embed해서 쓰는 B2B 계약 고객사. 자기 RAG Knowledge Base, Visitor Memory를 어드민 페이지에서 직접 관리. `Tenant.name`은 표시명이며 전역 unique다(대소문자·공백 무관 충돌 처리). 로그인에는 쓰지 않는다 — 고객은 sandcorp-auth로 로그인하고 Membership으로 조직에 들어온다. 공개 챗봇 URL용 Slug와는 별개다.

**Tenant Admin / Tenant Member**
Membership의 권한 등급. **Tenant Admin**은 조직 내 모든 권한을 가진다(팀원 관리·TENANT_KEY 회전·Slug 변경 포함). **Tenant Member**는 일상 운영 권한 전체를 가지되 *조직 단위·되돌리기 어려운* 행위는 제외한다 — 팀원 관리, TENANT_KEY 회전, Slug 변경, 흐름 편집·배포, Provider 키·base_url·모델과 웹훅 주소(시스템 프롬프트·상담 문구는 Member도 고친다). 권한 검사는 역할이 아니라 **Permission** 단위로 이뤄지며, 역할은 그 권한 집합의 프리셋이다(추후 세분화 여지). 한 조직은 항상 최소 1명의 활성 Tenant Admin을 유지한다(마지막 Admin은 비활성화·강등 불가 — lockout 방지).
_Avoid_: Operator(플랫폼 운영자 — Tenant 외부); "관리자"만으로 Operator/Tenant Admin 혼용 금지

**Permission**
authz의 최소 단위(권한 비트). 엔드포인트는 역할이 아니라 필요한 Permission으로 가드된다(예: 팀원 관리=`agents.manage`, 키 회전=`tenant_key.rotate`, Slug 변경=`slug.change`). Role은 Permission 묶음의 프리셋이라, 나중에 per-member 세분화를 도입해도 가드 지점은 불변이다.

**Visitor**
Tenant 사이트에서 챗봇을 실제로 사용하는 최종 사용자. Tenant 시스템의 회원 ID일 수도 있고, 익명 사용자일 수도 있음.

## Authentication & Session

**Tenant Slug**
Tenant의 챗봇 공개 URL(`/chatbot/{slug}/`)에 쓰이는 고유·URL-safe 공개 식별자. 사람이 읽는 표시명(`Tenant.name`)과 별개이며 전역에서 중복 불가. Tenant가 어드민에서 직접 설정·변경하되, 변경하면 사이트에 박아둔 기존 임베드 URL이 끊긴다.
_Avoid_: 표시명(name은 사람용, slug는 URL 식별자); UUID(slug는 사람이 정하는 짧은 이름)

**Anonymous Visitor ID**
위젯이 생성해 브라우저 localStorage에 저장·재사용하는, 추측 불가한 랜덤 VisitorId. Tenant가 `?visitor_id=`로 식별값을 주지 않은 익명 방문자의 정체성으로, 세션을 넘어 지속되어 같은 브라우저에서 대화 이력·Visitor Memory가 축적된다. visitor_id 슬롯은 항상 채워지며 출처만 다르다(Tenant 제공=식별 / 위젯 생성=익명).
_Avoid_: session_id(이건 정체성이지 연결 단위 세션이 아니다)

**Identity Verification**
식별된(Tenant 유저시스템의) Visitor의 visitor_id 위조를 막는 선택적(opt-in) 검증. Tenant별 토글로 켜며, 켜지면 서버가 `HMAC(tenant secret, visitor_id)` 해시를 요구·검증한다. 해시는 안정값(visitor_id당 결정적)이라 유저당 1회 계산해 캐시하며, Operator 백엔드의 HMAC API(TENANT_KEY 인증)로 받거나 Tenant가 직접 계산한다. 옛 EmbedToken의 per-session 발급 왕복을 대체한다.
_Avoid_: EmbedToken(per-session 단기 토큰 — 폐지됨); 토큰(해시는 무상태·무기한)

**TENANT_KEY**
Tenant를 식별하는 서버사이드 전용 시크릿. 절대 브라우저에 노출되지 않음. 두 가지 용도로만 사용: (1) Tenant 서버가 Identity Verification HMAC API를 호출할 때, (2) **break-glass** — Admin 등가 주체로 팀원·초대 API를 부를 때(마지막 Admin을 잃은 조직이 TENANT_KEY로 **Admin 초대 링크**를 발급해 복구한다, ADR-0025 D). 어드민 UI 로그인에는 사용하지 않음.

**초대 링크** (Invite)
Tenant Admin(또는 TENANT_KEY)이 만드는 팀원 초대. 추측 불가 토큰(256비트, 해시만 저장)·만료 7일·1회용·역할 지정이다. 받은 사람이 링크(`/api/auth/authorize/member?invite=…`)로 sandcorp-auth에 로그인하면 그 Tenant의 Membership이 생긴다. 이메일은 보내지 않는다 — Admin이 직접 전달한다. Operator가 Tenant를 만들 때도 첫 Admin 초대 링크가 나온다.
_Avoid_: 임시 비밀번호(폐지 — Flow는 비밀번호를 만들어 주지 않는다), 공개 셀프 가입(폐지 — 테넌트 만들기는 온보딩)

**Access Token**
어드민 UI(Operator·멤버)가 API 호출에 쓰는 단수명 Bearer 자격증명. 탈취 시 피해를 줄이려 짧게(30분) 유지하며 브라우저 sessionStorage에만 둔다. 만료되면 Refresh Token으로 무중단 재발급한다.
_Avoid_: Refresh Token(장수명·재발급 전용); 24시간 JWT 단일토큰(구 방식 — 폐지)

**Refresh Token**
Access Token을 재발급받기 위한 장수명 자격증명. httpOnly 쿠키로만 전달되어 JS가 읽을 수 없다(XSS 탈취 차단). 어드민 로그인 세션 1건당 1개 발급되며 사용할 때마다 회전(rotation)되고, 서버가 보관·취소할 수 있다(무상태 JWT와 달리 만료 전 강제 폐기 가능).
_Avoid_: Access Token(단수명·API 호출용); TENANT_KEY(위젯용 서버사이드 키 — 사람 로그인 세션 아님)

**Session Family**
한 번의 로그인에서 시작되는, 한 기기(브라우저)의 Refresh Token 회전 체인. "로그인 1회 = Family 1개"이며 한 주체(Operator/Membership)는 여러 기기에서 동시에 여러 Family를 가질 수 있다. 회전·재사용 감지·강제 폐기가 모두 Family 단위라 한 기기의 토큰 도난이 다른 기기 세션으로 번지지 않는다. 최초 로그인 시점 +14일 **절대 만료 상한**을 가지며 회전해도 연장되지 않는다(탈취자가 무한 갱신으로 세션을 영속화하는 것을 막는다). 「모든 기기에서 로그아웃」이나 멤버 비활성화 시 해당 주체의 전 Family가 일괄 폐기된다. 멤버 세션의 주체는 사용자가 아니라 Membership이다 — 조직마다 세션이 따로다.
_Avoid_: 슬라이딩 세션(절대 상한이라 회전이 만료를 밀지 않음); 단일 세션(주체당 여러 기기 Family 공존)

**EmbedToken** _(폐지 예정 — A1)_
~~Operator가 발급하는 단기 서명 토큰 (TTL 보유). Tenant 서버가 `TENANT_KEY + VisitorId`로 발급 요청하면 생성됨. iframe src URL에 포함되어 Visitor 브라우저에 전달.~~ 공개 Tenant Slug URL + 선택적 Identity Verification으로 대체됨(ADR-0011). A1 구현 시 항목 제거.

**ChatSession**
Visitor가 `/chatbot/{slug}/`로 최초 연결할 때 생성되는 세션. VisitorId(Tenant 제공 식별 또는 Anonymous Visitor ID)에 귀속되며, 세션 이후 채팅 이력은 ChatSession 단위로 저장됨.

**VisitorContext** _(폐지 예정 — A1)_
~~EmbedToken 발급 시 Tenant가 함께 넘기는 Visitor의 정적 메타데이터.~~ A1에서 폐지(연결 시점 신뢰 채널 제거; 첫 메시지 개인화 상실, 대체는 Visitor Memory). 비신뢰 입력 채널을 없애 프롬프트 인젝션 표면도 축소.

## Knowledge & Memory

**RAG Knowledge Base**
Tenant별 지식그래프(Knowledge Graph). Tenant가 업로드한 문서에서 추출한 Entity와 그 관계, 그리고 Community 요약으로 구성됨. 응대 매뉴얼 등이 원천이며, Visitor 무관·Tenant 전체에 적용됨.
_Avoid_: 벡터 인덱스(이제 그래프가 1차 구조이며 벡터는 그 위의 검색 보조 수단)

**Knowledge Graph**
RAG Knowledge Base의 1차 구조. 노드는 Entity, 엣지는 Entity 간 관계. 각 노드/관계는 어떤 Document에서 추출됐는지(출처)와 소속 Tenant를 보유한다.

**Entity Mention**
문서에서 LLM이 추출한 개별 언급(이름 + 그 문맥의 설명 + 출처 Document). 같은 표기라도 맥락이 다르면 별개의 Mention이다(한강 "다리"와 신체 "다리"는 다른 Mention). Entity Equivalence를 거쳐 Entity로 묶이기 전의 원자료다.
_Avoid_: Entity(동치로 묶기 이전 단계는 Mention)

**Entity**
같은 실세계 대상(referent)을 가리키는 Entity Mention들이 동치로 묶인 정체(예: 특정 제품, 사양, 액세서리). **정체성은 이름이 아니라 맥락(설명·이웃 관계·출처)으로 결정된다** — 같은 이름이라도 맥락이 다르면 다른 Entity(한강 "다리" vs 신체 "다리"), 이름이 달라도 맥락이 같으면 같은 Entity("FCB1010"="FCB-1010"). 이름과 설명으로 **의미 검색**이 가능하며, 한↔영 등 다국어·동의어 질의도 매칭된다(예: "메뉴" → "OSD Menu").
_Avoid_: 벡터 인덱스; "이름으로 식별"(이름은 약한 보조 신호일 뿐)

**Entity Equivalence**
두 Entity Mention이 같은 referent를 가리키는 동치 관계. 이름 유사도만으론 불충분하며(동음이의·표기변이가 이름을 오도한다) 맥락 정합으로 판별한다. 동치인 Mention들이 하나의 Entity를 이룬다.
_Avoid_: 정규화(텍스트 정규화와 혼동); 병합(노드를 물리적으로 합치지 않는 비파괴 동치다)

**Community**
Knowledge Graph에서 밀접하게 연결된 Entity 묶음. 각 Community에는 LLM이 생성한 요약(Community 요약)이 붙어, 글로벌/요약형 질의에 사용된다.

**Local Search**
특정 Entity와 그 이웃(관계로 연결된 Entity·출처 문서 조각)을 탐색하는 검색. "이 제품의 사양은?"처럼 한 대상에 집중된 질의에 사용.

**Global Search**
Community 요약들을 가로질러(map-reduce) 답을 구성하는 검색. "공통으로 권장하는 설정은?"처럼 전체/요약형 질의에 사용. Local Search보다 비싸다.

**Document Label**
Tenant가 문서에 부여하는 사용자 편집 가능한 식별 이름. 업로드 시 파일명을 기본값으로 채우며, 이후 언제든 수정 가능. `Document.name` 필드에 저장됨. Knowledge Graph에서 해당 문서를 대표하는 Entity의 이름이자, 그 문서에서 추출된 노드/관계의 출처 표시에 쓰인다. (옛 벡터 RAG의 "청크 임베딩 prefix + 재임베딩" 메커니즘은 GraphRAG의 Entity 추출로 대체됨 — 본문에 없는 제품명도 Entity로 잡히므로 prefix가 불필요.)
_Avoid_: 파일명, 문서명, title

**DocumentIngester**
문서에서 텍스트를 추출한 뒤 그 텍스트를 Knowledge Graph 기여분(Entity·관계)으로 변환·저장하는 인터페이스. PDF/TXT/이미지(PNG·JPG·WEBP)/Excel(xlsx·xls)/웹 URL을 지원. 텍스트 추출 단계: PDF는 추출 후 **단어 수 부족 또는 깨진 추출(Garbled Extraction) 감지 시 OCR로 재추출(fallback)**, 이미지는 OCR, **Excel은 시트별 헤더-키 행별 텍스트로 평탄화**, **웹은 명시적 URL(들)을 fetch해 메인 콘텐츠 추출(보일러플레이트 제거; 재귀 크롤 아님)**. 추출된 텍스트는 LLM Entity/관계 추출을 거쳐 그래프에 기여하며, 각 기여 노드/관계에는 출처 Document가 기록된다.
_Avoid_: OCR Ingester (이미지 전용이 아닌 PDF fallback도 포함하므로 ImageIngester/PDFIngester 클래스명을 사용); "벡터로 변환"(이제 그래프 기여가 1차 산출물); 재귀 크롤러(웹은 명시적 URL 가져오기지 링크 추적 크롤이 아님)

**Document Source**
Document의 원천 종류: **파일**(업로드되어 media에 저장) 또는 **웹 URL**(명시적으로 지정되어 fetch됨). 인제스션 태스크가 소스 종류로 분기한다(파일은 디스크 읽기, URL은 fetch). 웹 소스 Document의 갱신은 one-shot이며, Tenant가 어드민에서 수동 "다시 가져오기"로 재-ingest한다(자동 스케줄 재크롤 없음).
_Avoid_: 크롤(명시적 URL 가져오기지 재귀 크롤이 아님)

**Garbled Extraction**
PDF 텍스트 레이어의 폰트 인코딩(ToUnicode/CID 매핑) 부재로 글리프가 의미 없는 문자열(mojibake)로 추출된 상태. 원문 정보가 텍스트 레이어에서 소실되어 LLM 정제로는 복원할 수 없고(추측=창작이 됨) OCR(픽셀 재인식)로만 되찾을 수 있다. 추출 직후 문자 클래스 비율 휴리스틱으로 문서 단위 감지하며, 감지 시 OCR 재추출이 트리거된다.
_Avoid_: 인코딩 오류(너무 일반적), 깨진 글자

**Text Unit**
문서를 일정 크기로 나눈 텍스트 조각. Knowledge Graph의 노드로 저장되며 임베딩을 가져 Local Search의 근거 문맥(citation)으로 쓰인다. 어떤 Entity들이 이 조각에서 추출됐는지, 그리고 출처 Document와 연결된다. citation은 검증의 기준점이므로 **추출 원문(또는 OCR 재추출본)에 충실**해야 하며 LLM이 정제·생성한 텍스트를 담지 않는다 — 이 점에서 LLM 해석물인 Entity와 구분된다(Garbled Extraction 참조).
_Avoid_: DocumentChunk(옛 pgvector 모델의 명칭 — GraphRAG에선 그래프 노드인 Text Unit), chunk(단독 사용 시 모호)

**Graph Freshness**
Tenant Knowledge Graph의 전역 신선도 상태: `fresh`(Community 요약이 최신) / `stale`(문서 추가·삭제로 재구축 필요) / `rebuilding`(재구축 중). 문서별 처리 상태(`Document.status`)와 별개이며, Global Search의 최신성을 나타낸다. stale이어도 직전 Community 요약으로 Global Search는 동작한다.

**Visitor Memory**
특정 Visitor에 대해 ChatSession을 넘어 축적되는 장기 기억. LLM이 대화 중 자동 추출하며, 어드민이 조회·수정·삭제 가능.

**Conversation Memory**
단일 ChatSession 내의 대화 히스토리. LangGraph Checkpoint로 관리됨. `thread_id = session_id`로 세션별로 구분되며, 수동 히스토리 로드 없이 LangGraph가 이전 state를 자동 복원함.

**LangGraph Checkpoint**
LangGraph가 PostgreSQL에 저장하는 그래프 실행 state 전체 스냅샷. `thread_id = session_id`로 ChatSession에 귀속됨. 매 LLM 호출 후 자동 저장되며, 다음 호출 시 자동 복원됨. ChatMessage와 별개로 존재하며 ChatMessage는 Visitors 탭 조회용으로 별도 유지됨.

## Tenant Configuration

**TenantConfig**
Tenant별 설정 집합. 어드민에서 Tenant가 직접 관리. 포함 항목:
- `model_id`: (폐지 예정 — C) LLM Provider의 `chat_model`로 흡수됨
- `system_prompt`: Tenant가 직접 편집하는 Base System Prompt. Operator가 기본 템플릿을 제공하며 Tenant가 수정 가능. RAG 결과·VisitorContext·Visitor Memory는 이 프롬프트에 자동 주입됨
- `welcome_message`: ChatWidget이 열릴 때 Visitor에게 자동으로 표시되는 환영 메시지. 비어 있으면 표시하지 않음. SSE `connected` 이벤트 payload에 포함되어 전달됨
- `brand_name`: 위젯 헤더 상단에 표시되는 Tenant 브랜드 텍스트(이미지 로고 아님). 비면 AI/사람 상태 텍스트만 표시. AI/사람 상태는 보조 텍스트+점으로 강등. SSE `connected` payload로 전달됨
- `agent_display_name`: HITL 모드에서 Visitor 위젯에 표시되는 상담원 발신자 이름. 예: "ABC쇼핑 고객센터"
- `hitl_enabled`: 이 Tenant가 HITL(사람 상담원 전환)을 쓸지 여부(기본 켜짐). 꺼지면 에이전트 그래프가 escalation 분기 없는 토폴로지로 로드되어 AI가 절대 사람에게 넘기지 않는다
- `webhook_url`: HITL 발생 시 알림을 보낼 웹훅 URL. https·공용 주소만(내부망·사설 주소는 보내지 않음, #108)
- `webhook_type`: 웹훅 플랫폼 종류. `slack` / `discord` / `generic` 중 하나. 플랫폼별로 페이로드 포맷이 다르게 전송됨

**WebhookConfig**
TenantConfig 내 웹훅 설정 집합. HITL 발생 시 Slack·Discord·Generic 엔드포인트로 알림을 발송하기 위한 정보. 알림 페이로드에는 HITL 트리거 유형, 마지막 N개 대화, 어드민 UI 딥링크가 포함됨.

**LLM Provider**
Tenant가 자기 키로 비용을 부담하는 챗·추출 LLM 제공자 설정. 타입은 **OpenAI · Claude · Custom**. OpenAI/Custom은 OpenAI-호환 클라이언트, Claude는 Anthropic 네이티브 클라이언트로 분기. Custom은 `base_url`+크레덴셜로 임의 OpenAI-호환 엔드포인트(OpenRouter·Deepinfra·자체 호스팅 등) 지정 — 운영은 https·공용 주소만(저장 때와 부를 때 모두 해석한 IP를 검사, 내부망·사설 주소는 dev만 — #108). `chat_model`·`extraction_model`을 가짐. LLM Provider 변경은 재구축을 유발하지 않는다(임베딩 공간 불변).
_Avoid_: model_id(이제 LLM Provider의 chat_model로 흡수); OpenRouter 전용(provider는 다중)

**Embedding Provider**
Tenant가 자기 키로 비용을 부담하는 임베딩 제공자 설정. 타입은 **OpenAI · Custom**만(Claude는 임베딩 API가 없어 제외). LLM Provider와 **독립**. 임베딩 모델이 벡터 공간을 정의하므로, Embedding Provider 변경은 저장된 벡터를 무효화 → **재임베딩 재구축**(그래프 구조 보존, 무중단 swap, `Graph Freshness=rebuilding`)을 유발한다. 프로덕션엔 플랫폼 기본 폴백이 없어(GPU 없는 Oracle A1) Tenant 설정이 필수. 벡터 인덱스는 가변 차원 때문에 per-Tenant로 격리된다.
_Avoid_: LLM Provider와 동일시(독립 설정); 차원만으로 호환 판단(모델 정체성이 공간을 정함)

## Human-in-the-Loop (HITL)

**Escalation**
ChatSession이 AI 응대에서 사람 응대로 전환되는 이벤트이자 상태. 트리거는 두 가지: (1) LLM이 Structured Output으로 `needs_hitl: true`를 반환할 때(AI 불확실 판단 + Visitor의 상담원 요청 키워드 감지 통합), (2) Visitor가 명시적으로 상담원 요청 키워드를 입력할 때. Escalation 상태: `pending` → `claimed` → `resolved`.

**EscalationClaim**
Tenant 팀원이 특정 Escalation의 처리를 수락한 행위 및 기록. 클레임된 Escalation은 해당 클레이머에게 잠기고, 다른 팀원은 읽기 전용. 클레이머가 "AI에게 넘기기"를 누르면 Escalation이 `resolved` 상태로 전환되고 ChatSession은 AI 모드로 복귀.

**HITL 모드**
Escalation이 `pending` 또는 `claimed` 상태인 동안 ChatSession이 처하는 운영 상태. 이 모드에서는 AI가 완전히 침묵하고, Visitor 위젯에 시스템 메시지("상담원 연결 중" 또는 "상담원과 연결되었습니다")가 표시됨. Visitor는 HITL 전환 사실을 알 수 있음.

**HumanTurn**
HITL 모드에서 Tenant 상담원이 Visitor에게 보내는 메시지. ChatMessage와 동일한 구조이나 `role`이 `human_agent`로 저장됨. Visitor 위젯에서는 TenantConfig의 `agent_display_name`으로 표시됨.

## Identity (sandcorp-auth)

**External Identity**
Flow 사용자의 신원. sandcorp-auth가 발급한 액세스 토큰의 `(iss, sub)` 쌍이며 Flow 사용자 행의 유일 키다. Flow는 비밀번호를 갖지 않는다. 표시 이름은 캐시일 뿐 신원이 아니다(ADR-0028).
_Avoid_: username·tenant_name을 신원으로 쓰기, 「계정」(Flow에는 자체 계정이 없다)

**Membership**
External Identity(사용자)와 Tenant의 연결과 그 역할(Tenant Admin / Tenant Member)·활성 여부. 옛 TenantAgent(조직마다 따로 둔 username/password 계정)를 대신한다. 테넌트 역할은 sandcorp-auth 스코프가 아니라 Flow 안의 이 행으로 관리하며, 권한 비트 가드와 「마지막 Admin 보호」를 그대로 따른다. HITL `EscalationClaim.claimed_by`도 Membership을 가리킨다(멤버십이 지워지면 비운다). 로그인 콜백은 멤버십이 하나면 그 조직 세션을, 여럿이면 조직 선택을, 없으면 「온보딩 필요」를 돌려준다. 사용자가 sandcorp-auth에서 탈퇴하면 탈퇴 웹훅(`user.deleted`, `/api/auth/webhook`)을 받아 `forget_user(iss, sub)`가 사용자·멤버십·세션을 지운다. 마지막 Admin이 탈퇴해도 Tenant는 그대로 두고, TENANT_KEY로 Admin 초대를 다시 내어 복구한다.
_Avoid_: 스코프로 역할 표현

**Resource Token**
Flow API가 받는 sandcorp-auth 액세스 토큰(`aud` = Flow 리소스). 검증 규칙은 sandcorp-auth `docs/resource-server-token-validation.md`를 그대로 따른다. 관리 화면은 로그인 콜백에서 한 번 검증한 뒤 Flow 자기 세션(Access/Refresh)으로 돈다.

**MCP Token**
MCP 서버(`/mcp`)가 받는 Resource Token. `aud`가 API(`…/api`)와 다른 MCP 리소스(`…/mcp`)라 서로의 토큰은 통하지 않는다. 스코프는 4개(`flow.read`·`flow.write`·`flow.deploy`·`flow.admin`, 연결할 때 받는다)이고 **상한**일 뿐이다 — 무엇을 할 수 있는지는 Membership 역할이 정한다(도구마다 등록된 권한 비트: 흐름 편집·배포·승인·팀 관리는 Tenant Admin만, 설정 수정과 문서·방문자 기억 쓰기는 Member도). 스코프가 모자라면 커넥터를 다시 연결한다. `deploy_version`·`rollback_flow`의 옛 스코프(`flow.write`) 호환 기간은 끝났다(#220) — 옛 연결은 403이고 재연결로 푼다. 대표 토큰도 멤버십이 있는 테넌트만 다룬다 — Operator 권한은 MCP에 없다. MCP는 Flow 세션을 만들지 않고 요청마다 토큰을 검증한다.
_Avoid_: MCP 토큰으로 Operator 기능, 스코프로 역할 표현

**MCP Audit Event**(감사 로그)
MCP로 부른 쓰기 도구 한 번의 기록(시각·테넌트·사용자·토큰 `sub`·`client_id`·`jti`·도구·인자·결과 코드). 성공·거부·실패 모두 남고, 기록이 안 되면 그 도구는 실행되지 않는다. 읽기 도구는 남지 않는다. 테넌트 Admin만 읽는다(`audit.read`). 보존 180일(`MCP_AUDIT_RETENTION_DAYS`)이 지나면 지운다. 2단계 도구는 미리보기·확정 단계도 남는다(토큰 값은 가린다). `legacy_scope` 칸은 호환 기간(끝남, #220)의 지난 기록용이라 이후 늘 false다. 결과 기록만 실패하면 행이 `started`(결과 모름)로 남는다. ADR-0033.
_Avoid_: 이벤트(Event Store의 도메인 이벤트와 다르다), 접근 로그, 읽기 도구도 남는다고 말하기

**2단계 확인(MCP)**
되돌리기 어려운 MCP 쓰기 도구의 호출 규약. 처음 부르면 실행하지 않고 미리보기와 `confirm_token`을 돌려주고, 같은 인자에 그 토큰을 실어 다시 부르면 실행한다. 토큰은 그 도구·테넌트·사용자·인자에만, 5분 동안, 한 번만 통한다. 미리보기도 스코프·권한 검사를 지난 뒤에만 나온다. 비밀·Provider·웹훅·초대 만들기는 MCP 도구 자체가 없다. 모델이 미리보기를 사용자에게 보이게 유도할 뿐 사람의 확인을 강제하지 않는다 — 사람 쪽 관문은 클라이언트의 도구 승인 화면이다.
_Avoid_: 2단계로 사람 승인을 보장한다고 말하기

## Flow

**Flow**
테넌트가 편집기에서 그리는 상담 흐름. 노드와 선으로 이뤄지며, 하나의 초안과 여러 Flow Version을 가진다. 위젯(공개 slug)은 테넌트가 정한 Flow의 배포 중 버전으로 돈다.
_Avoid_: 시나리오, 봇, 워크플로(Workflow는 다른 제품 용어)

**Flow Version**
Flow를 굳힌, **바뀌지 않는** JSON(노드·선·노드 설정). 저장 뒤 고치지 않는다. ChatSession은 시작할 때의 버전에 고정되어 끝까지 그 버전으로 돈다(ADR-0027).

**Draft**
Flow마다 하나 있는 고칠 수 있는 작업본. 편집과 시험 대화는 초안에 대해 한다. 틀려도 저장되고(검증 결과를 함께 돌려준다), 「버전 저장」이 검증을 통과한 초안을 새 Flow Version으로 굳힌다.
_Avoid_: 임시 저장본

**Deployment / Rollback**
배포는 Flow의 「배포 중 버전」을 정하는 동작이다. 배포 절차는 **초안 → 시험 → 버전 저장 → 배포**. 실행기에 핸들러가 없는 노드가 있는 버전은 배포할 수 없다. 되돌리기는 이전 버전을 배포 중으로 바꾸는 한 동작이다(버전이 새로 생기지 않는다). 규칙은 공용 서비스 층 `apps/flows/services.py` 한 곳에 있고 MCP·흐름 API·편집기가 그것을 감싼다.

**Deploy Request**
테넌트 설정 「배포에 관리자 승인 필요」(`deploy_requires_approval`)가 켜졌을 때만 생기는 단계. 배포를 시도하면(HTTP·MCP 모두) 배포 대신 요청이 생기고, **요청자가 아닌 다른 Admin**(또는 TENANT_KEY)이 승인하면 그 버전이 배포된다. 흐름마다 대기 요청은 하나(새 요청이 앞의 것을 대체). 되돌리기는 승인 대상이 아니다(긴급 복구). 편집·배포·승인 권한은 비트 `flow.edit`·`flow.deploy`·`flow.approve`로 모두 Admin 전용이다(Member는 읽기·판단 기록 조회).

**Executor**
대화 턴마다 Flow Version JSON을 LangGraph StateGraph로 해석해 돌리는 실행기. 체크포인트·HITL·SSE는 기존 것을 재사용한다. 운영 챗의 한 턴은 이것으로 돈다(#72) — 세션에 고정된 버전, 없으면 첫 턴에 공개 흐름 배포 중 버전에 고정, 공개 흐름이 없으면 기본 흐름.
_Avoid_: 컴파일러(코드를 만들지 않는다)

**Node / Node Kind / Node Handler**
노드는 흐름의 한 단계, Node Kind는 그 종류, Node Handler는 종류마다 하나인 파이썬 핸들러다. 첫판 종류: **시작**, **AI 답변**, **조건**, **사람 넘김**, **지식 검색**(GraphRAG), **AI 분류·추출**(확신도를 함께 냄), **가드레일**, **HTTP 도구**. 편집기에서 노드는 N-코드(예: N-03)를 단다.

**Turn Boundary**
방문자 답을 기다리는 노드(지금은 `await_reply`가 켜진 AI 답변). 실행기는 여기서 답을 보내고 턴을 끝내며, 다음 턴은 그 노드의 나가는 선에서 잇는다. 흐름의 순환은 이것을 하나 이상 지나야 한다(ADR-0027 보충, `docs/flow-json.md`).

**Default Flow(기본 흐름)**
시작 → 지식 검색 → AI 답변 →(AI가 넘김을 판단하면 `handoff` 갈래로) 사람 넘김. 옛 코드 그래프(build_graph)와 같은 동작이며, 흐름이 하나도 없는 테넌트에 배포 중·공개 흐름으로 만들어 둔다(#23).

**Guardrail**
금지어·개인정보·가격 약속 같은 표현을 막거나 다시 쓰는 노드. 편집기에서는 황·흑 경고띠로 표시한다.

**Confidence**
AI 분류·추출 노드가 결과와 함께 내는 확신도(0~1). 조건 노드가 이 값으로 분기할 수 있다.

**Decision Trace**
판단 기록. 한 턴에서 노드마다 입력·출력·분기 이유·토큰·비용·걸린 시간을 남긴 기록. 시험 대화와 운영 대화가 같은 형식이며, 비밀 값은 가린다. 편집기에서 모노 글꼴로 보인다.
_Avoid_: 로그(운영 로그와 다르다), 트레이스(Langfuse와 혼동)

**Test Chat**
편집기 오른쪽(또는 MCP `test_turn`)에서 초안에 대해 하는 시험 대화. 판단 기록(토큰·비용)을 함께 보여 준다. 실제 사람 넘김 알림은 보내지 않는다 — 넘김 노드는 「넘겼다」고만 표시한다(dry run). 대화는 시험 세션(`ChatSession.is_test`)에 쌓여 운영 세션 목록·방문자 메모리에 섞이지 않는다. 체험 테넌트면 시험 한 턴도 체험 턴 1을 쓴다. 편집기·/try의 시험 대화 SSE는 턴 도중에 노드 이벤트(`node_start`·`node_end`·`node_error`)를 실시간으로 보낸다 — 실행기가 시험 턴에만 내고 운영 대화에는 없다(ADR-0035).

**HTTP Tool**
흐름에서 외부 HTTPS API를 부르는 노드. 테넌트의 **Domain Allowlist**에 있는 호스트만, 해석 뒤 사설·루프백·링크로컬·메타데이터 IP 차단, 리다이렉트마다 재검사, 5초·256KB 상한(ADR-0029). 호스트는 틀의 고정 부분이고 상태 값은 URL 경로·쿼리·본문에만(인코딩·이스케이프). 응답 JSON 경로 값을 상태 `fields`에 저장하고(`save`), 실패는 `error` 갈래로 간다(이유 코드는 판단 기록). 비밀을 실은 요청은 다른 호스트로 리다이렉트되지 않는다.

**Tenant Secret**
HTTP 도구 등에서 쓰는 테넌트 비밀 값. 암호화해 저장하고, 흐름 JSON에는 `{{secret.이름}}` 참조만 둔다. 조회는 이름만 되고 값은 쓰기 전용이다. 치환은 요청을 만들 때 서버에서만 하고, 판단 기록·로그·오류 메시지에서는 원문과 그 부분 문자열(6자 이상)을 `[비밀]`로 가린다. 테넌트가 읽는 출력(판단 기록·시험 대화 trace)은 그 테넌트가 저장한 비밀 전체(DB에서 읽음, 짧은 캐시)와 그 파생값으로만 가리고, 운영자만 보는 로그는 그 프로세스가 본 모든 테넌트 비밀로 가린다. 같은 테넌트 Member가 시험 대화로 자기 테넌트 비밀 조각을 맞춰 보는 것은 받아들인 위험이다(값 열람·쓰기는 Admin만). 없는 이름을 참조한 흐름은 검증 오류(`unknown_secret`). 관리(만들기·교체·지우기)는 Admin(`secrets.manage`).

**Domain Allowlist**
테넌트가 등록한 「허용 도메인」 — HTTP 도구가 부를 수 있는 호스트. 정확한 호스트(`api.example.com`) 또는 한 단계 하위 와일드카드(`*.example.com`)만 저장된다(스킴·경로·포트·IP 거부, `*.com` 거부). 관리는 Admin(`domains.manage`).

**Demo API (가짜 API)**
예제 흐름·`/try`의 HTTP 도구가 부르는 결정적 엔드포인트(주문 조회·레벨 테스트 예약·문의 접수 흉내). 예약 호스트 `https://demo-api.flow.sandcorp.co.kr` 글자 그대로일 때만 HTTP 도구가 네트워크 없이 같은 프로세스에서 처리하고, 모든 테넌트에 허용 도메인 없이 허용된다(대문자·포트·하위 도메인 같은 변형은 일반 경로). 상태를 저장하지 않고, 실패 흉내(번호 `9500`·`9408`, `?simulate=`)로 error 갈래를 보여 줄 수 있다. 실제 API처럼 같은 실패 코드·`save` 규칙을 거친다.

## Plans & Trial

**Platform Key**
플랫폼(대표)이 비용을 내는 **OpenAI** 키 하나. 체험 한도, `/try`, 「Sandcorp」 테넌트(sandcorp.co.kr 홈 위젯)만 이 키를 쓴다. 모델은 설정값(`PLATFORM_LLM_MODEL`, 기본 gpt-4o-mini급 저가 모델)이다. 플랫폼은 Anthropic 등 다른 공급자 키를 두지 않는다. 그 밖의 테넌트는 자기 Provider(BYOK)를 쓴다.

**Trial Quota**
가입한 테넌트에게 주는, 플랫폼 키로 도는 체험 한도(`TRIAL_TURNS`, 기본 200턴 — 1턴은 방문자 메시지 하나). 자기 LLM Provider가 없을 때만 쓰고, 다 쓰면 방문자에게는 「상담 준비 중」 문구만 보이며 테넌트가 자기 LLM Provider 키를 넣어야 흐름이 돈다.
_Avoid_: 무료 플랜(결제·요금제는 다음 판)

**Try Session**
로그인 없이 `/try` 편집기에서 **처음 저장·시험 대화·가입할 때** 브라우저 세션에 생기는 예제 흐름 사본(그 전의 편집은 화면에만 있다, ADR-0032). 노드 위치 이동만으로는 생기지 않는다. 24시간과 IP 하루 3세션은 이 첫 쓰기부터 센다. 편집과 시험 대화만 되고 배포는 안 되며 24시간 뒤 지운다. 제한: 플랫폼 키(OpenAI 저가 모델, `PLATFORM_LLM_MODEL`), 세션당 20턴, IP당 하루 3세션, 전체 하루 비용 $5(넘으면 「내일 다시」), HTTP 도구는 가짜 API만, 지식 검색은 정해 둔 지식 묶음만, 문서 업로드·실제 사람 넘김 알림 금지. 「이 흐름으로 가입」(로그인 + 그 브라우저의 세션 쿠키 둘 다)으로 사본을 내 테넌트의 **새 흐름(초안, 배포 안 함)**으로 옮긴다 — 새 가입자는 새로 만든 테넌트(기본 흐름 옆)에, Admin인 테넌트가 있으면 골라서. 그래프만 옮기고(지식 검색 노드·가짜 API URL은 그대로, 체험 도서관 예제 문서는 안 옮김) 옮기면 체험 테넌트·세션을 지운다(#42).
Try Session마다 **체험 테넌트**(숨은 테넌트, `kind=try`)가 하나 생기고 예제 사본(초안)이 들어간다. 24시간 뒤 테넌트째 지운다(ADR-0030).
브라우저는 세션 쿠키(`sf_try`, 추측 불가 ID — 서버엔 해시만)로 자기 체험 테넌트에만 닿는다(공개 API `/api/try`). 「하루」는 Asia/Seoul 날짜이고, 하루 비용은 체험 테넌트 정리와 무관한 날짜별 누적으로 센다(턴 전에 상한 미만인지 보고 턴 뒤 더하므로 한 턴만큼 넘을 수 있다).

**임베드 모드 (Embed Mode)**
`/try?embed=1`로 iframe에 넣은 체험 화면(ADR-0031). 머리줄을 숨기고 바깥 링크·「이 흐름으로 가입」을 새 탭으로 열며, 뷰포트 단위 없이 고정 높이로 그려 부모에게 `sandcorp-flow:height` 메시지로 높이를 알린다(부모가 먼저 `sandcorp-flow:hello`). 부모와 flow가 같은 사이트여야 한다. `template=home`이면 홈 편집기가 곧바로 열리고(「고쳐 보기 시작」 버튼 없음) 세션은 첫 쓰기에 생긴다(ADR-0032). 세션이 없을 때의 「이 흐름으로 가입」 새 탭은 중간 페이지 `/admin-ui/try/signup?template=…`(noindex·nofollow)가 세션을 만든 뒤 로그인으로 보낸다. 좁은 폭(929px 등)에서는 캔버스를 노드 목록보다 먼저 그린다. 위젯의 공개 챗봇 iframe(`/chatbot/{slug}/`)과는 별개다.

**체험 테넌트 (Try Tenant)**
Try Session 하나의 숨은 테넌트(`Tenant.kind = try`). 일반 목록·멤버십·운영 집계에 나오지 않고, 테넌트 Provider를 무시해 플랫폼 키로만 돌며,
HTTP 도구는 가짜 API만 부르고 문서 업로드·배포·실제 사람 넘김이 막힌다(이유 코드 `try_restricted`). 지식은 체험 도서관의 자기 예제 묶음만 본다.
_Avoid_: Trial 테넌트(가입 테넌트의 체험 한도 Trial Quota와 헷갈린다)

**체험 도서관 (Try Library)**
예제 지식 묶음을 한 번 적재해 두는 공유 숨은 테넌트(`kind = try_library`, `seed_try_library`). 체험 테넌트의 검색은 이 도서관 그래프를 그 예제의 문서로만 좁혀 읽는다.

**Template Flow**
체험 시작 템플릿 겸 소개 페이지 사례로 쓰는 예제 흐름: ① Sandcorp 홈 상담 에이전트, ② 쇼핑몰 배송·반품 상담, ③ 학원 수강 상담·레벨 테스트 예약. 코드에 고정된 흐름 JSON과 작은 지식 묶음(문서 셋)이 한 쌍이며(`example_catalog`), ②③의 HTTP 노드는 가짜 API만 부른다. ①은 Sandcorp 테넌트의 실제 홈 흐름으로도 그대로 시드된다 — 문의는 받지 않고 홈페이지 문의 폼으로 안내만 하며 HTTP 노드가 없다(HTTP 도구 시연은 ②③).

**공개 흐름 보기 (Public Flow View)**
테넌트가 켜면(설정 「공개 흐름 보기」, 기본 꺼짐, Admin만) 로그인 없이 누구나 그 테넌트 공개 흐름의 **배포 버전**을 읽기 전용으로 보는 화면(`/admin-ui/flows/{slug}`). 노드 설정은 노드 종류별 허용 목록으로만 내보내고 비밀 참조가 든 값·HTTP 헤더·바디·내부 URL·금지어는 빠진다. 배포 그래프가 Template Flow 그대로면 「고쳐 보기」로 그 체험(`/try?template=`)에 잇는다(이동만, 세션은 첫 쓰기에). 「Sandcorp」 테넌트는 시드가 켠다.
_Avoid_: 공개 편집기, 흐름 공유(편집·복사는 없다)
