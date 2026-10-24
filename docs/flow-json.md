# 흐름 JSON(Flow JSON)

Flow의 초안(Draft)과 Flow Version이 담는 JSON의 형식. 결정은 [ADR-0027](adr/0027-flow-json-interpreted-executor.md),
HTTP 도구 보안은 [ADR-0029](adr/0029-http-tool-node-security.md). 용어는 `CONTEXT.md` 「Flow」 절.

- 스키마·검증 코드: `backend/apps/flows/schema.py` (`validate_flow`, `parse_flow`, `flow_hash`, `NodeKind`)
- 모델: `backend/apps/flows/models.py` (`Flow`, `FlowDraft`, `FlowVersion`), `ChatSession.flow_version`
- 예제 파일: `backend/apps/flows/examples/sandcorp_home.json` (시험이 유효함을 확인한다)

## 문서 모양

```json
{
  "nodes": [
    {"id": "start", "kind": "start", "code": "N-01", "name": "시작",
     "position": {"x": 0, "y": 0}, "config": {}}
  ],
  "edges": [
    {"from": "start", "to": "classify", "handle": "next"}
  ]
}
```

| 필드 | 뜻 |
|---|---|
| `nodes[].id` | 흐름 안에서 유일한 노드 id(1~64자). 선과 판단 기록이 이것으로 노드를 가리킨다. |
| `nodes[].kind` | 노드 종류(아래 표). |
| `nodes[].code` | 편집기에 보이는 N-코드(`N-01` 꼴, 흐름 안에서 유일). |
| `nodes[].name` | 편집기 표시 이름(선택). |
| `nodes[].position` | 편집기 좌표 `{x, y}`. |
| `nodes[].config` | 종류별 설정. 모르는 필드는 거부한다. |
| `edges[].from` / `to` | 나가는 노드 id / 들어가는 노드 id. |
| `edges[].handle` | 갈래 이름. 생략하거나 `null`이면 `next`. |

## 노드 종류와 config

종류마다 config 모양이 다르다. 모르는 필드는 거부한다.

| kind | 이름 | config | 나가는 갈래 |
|---|---|---|---|
| `start` | 시작 | `{}` | `next` |
| `ai_answer` | AI 답변 | `instructions: str = ""`, `await_reply: bool = false` | `next`, `handoff`(선택 — AI가 사람 넘김이 필요하다고 판단할 때만 이 선으로) |
| `condition` | 조건 | `branches: [{name, when}]`(1개 이상, 이름 유일, `else`·`next` 예약). `when`은 아래 「when 식」 | 각 `name` + `else` — **모두 선 필수** |
| `handoff` | 사람 넘김 | `reason: str = ""` | 없음 |
| `knowledge_search` | 지식 검색 | `top_k: int = 5`(1~20) | `next` |
| `ai_classify` | AI 분류·추출 | `instructions: str = ""`, `labels: [str] = []`(분류), `fields: [{name, description = "", type = "string"\|"number"\|"boolean"}] = []`(추출) — **둘 중 하나는 있어야** | `next` |
| `guardrail` | 가드레일 | `rules: ["banned_words" \| "pii" \| "price_promise"]`(1개 이상, 중복 불가), `action: "block" \| "rewrite" \| "mask" = "rewrite"`, `target: "output" \| "input" = "output"`, `banned_words: [str] = []`(`banned_words` 규칙이면 1개 이상), `block_message: str`(기본 「죄송합니다. 해당 내용은 안내해 드릴 수 없습니다.」). `price_promise`와 `mask`는 함께 못 쓴다. 자세한 동작은 아래 「가드레일」 | `next` |
| `http_tool` | HTTP 도구 | `method: "GET"\|"POST"\|"PUT"\|"PATCH"\|"DELETE" = "GET"`, `url: str`(https만, 호스트 고정, 2000자), `headers: {str: str} = {}`(20개), `body_template: str \| null = null`(16000자), `save: {이름: 경로} = {}`(20개). 자세한 동작은 아래 「HTTP 도구」 | `next`(2xx), `error`(선택 — 실패할 때) |

`next` 갈래는 선이 없어도 된다 — 거기서 턴이 끝난다. AI 답변의 `handoff` 갈래도 선택이다 — 선이 있고 테넌트가 사람 넘김을 켰고 상담시간일 때만 AI가 넘김을 판단하고(넘김 판단이 든 답변 스키마), 선이 없으면 넘김 없는 답변 스키마로 묻는다. AI 답변의 `instructions`는 시스템 프롬프트 맨 끝 「이 단계의 지침」으로 실린다(그 노드의 호출에만). **사람 넘김이 결정된 턴에는 모델이 쓴 답을 방문자에게 흘리지도 저장하지도 않는다** — 넘김 판단(`needs_hitl`)이 답(`response`)보다 먼저 나오는 스키마라, 넘기기로 했으면 그때 답을 막고 넘김 안내만 보인다.

### 가드레일

`target`의 글을 `rules`로 검사한다 — `output`: 직전 AI 답변(나가는 말), `input`: 방문자 입력(들어오는 말).

- **규칙**: `banned_words`(금지어 목록, 대소문자 무시 부분 일치)와 `pii`(한국 전화번호·주민등록번호·카드번호(Luhn 검사)·이메일)는 결정적 코드다. `price_promise`(가격·기간·결과를 확정·약속하는가)는 LLM이 판정한다 — 판정이 실패하면 통과시키지 않고 걸린 것으로 본다.
- **처리**: `block`은 `block_message`로 바꾼다. `mask`는 걸린 부분만 가린다(금지어는 `●`, 개인정보는 `[전화번호 가림]`). `rewrite`는 LLM으로 고쳐 쓰고, 고친 글에 결정적 규칙이 남거나 호출이 실패하면 `block_message`로 막는다. 걸리지 않으면 글을 그대로 둔다.
- **들어오는 말**: 가리기·다시 쓰기는 모델과 대화 기록이 보는 `user_message`를 바꾼다(원본 메시지 행은 이미 저장돼 있다). 막으면 `block_message`로 답하고 턴이 끝난다 — 뒤의 노드는 돌지 않는다.
- **스트리밍**: 바로 뒤에 `target: "output"` 가드레일이 붙은 AI 답변은 답을 토큰으로 흘리지 않고 모아 두었다가, 가드레일이 통과시킨(또는 바꾼) 글을 한 번에 내보낸다. 막힌 원문이 먼저 새지 않게 하려는 것이다. 이 때문에 `await_reply` AI 답변 바로 뒤에는 출력 가드레일을 못 붙인다(`guardrail_after_await_reply`). AI 답변이 아닌 노드를 사이에 두면 답은 이미 나간 뒤라 저장되는 글만 바뀌고 판단 기록에 `late`가 남는다.
- **판단 기록**: `record`에 `target`·`rules`·`hits`(걸린 규칙과 종류 — 걸린 원문은 남기지 않는다)·`action`·`reason`. LLM을 부르면 노드 토큰·비용에 잡힌다.

### AI 분류·추출 결과

노드는 기존 챗과 같은 Provider(체험 차감 포함)로 구조화 출력을 받아 흐름 상태에 쓴다.

| 상태 키 | 값 |
|---|---|
| `label` | 고른 라벨(`labels`가 없으면 `""`) |
| `fields` | `{필드 이름: 값}` — 못 뽑은 필드(null·빈 문자열)는 `null`. `false`·`0`은 누락이 아니다 |
| `confidence` | 확신도 0~1(범위 밖은 잘라냄). 호출이 실패하면 `label ""`·`confidence 0`으로 이어 가고 판단 기록 reason에 실패를 적는다 |

판단 기록(record)에는 입력·출력(`label`/`fields`/`confidence`)·`missing`(못 뽑은 필드 이름들)·reason이 남는다. 상태는 대화에 걸쳐 유지되고, 분류 노드를 다시 지날 때마다 덮어쓴다.

### when 식

조건 노드는 갈래를 **위에서부터** 계산해 처음 참인 `name` 갈래로, 하나도 없으면 `else`로 간다. 식은 Python 식의 작은 부분집합이다(`eval` 없이 직접 계산). 저장할 때 문법을 검사한다 — 오류는 `invalid_config`.

- 이름: `label`, `confidence`, `fields`(`fields.budget`), `user_message`. 그 밖의 이름은 오류.
- 값: `'문자열'`, 숫자, `true`/`false`/`null`, 목록 `['a', 'b']`.
- 비교: `==` `!=` `<` `<=` `>` `>=`. 포함: `'환불' in user_message`, `label in ['a', 'b']`, `not in`.
- 결합: `and` `or` `not` 괄호.
- 값이 없으면(분류 전·필드 누락) `null`로 읽는다. `<` `<=` `>` `>=`와 `in`은 `null`이 끼면 거짓이고 오류를 내지 않는다.

예) 확신도가 낮으면 사람에게: `confidence < 0.5`. 판단 기록에는 계산한 갈래들(`checked`)과 고른 갈래·이유(`'식'이(가) 참이라 '갈래' 갈래로`)가 남는다.

### HTTP 도구

테넌트 **허용 도메인**(설정 → 허용 도메인)에 있는 HTTPS 호스트만 부른다. 보안 계층(`apps/core/safe_http.py`, ADR-0029)이 해석한
모든 IP를 검사하고 그 IP로 연결하며, 리다이렉트마다 다시 검사한다. 5초·응답 256KB·리다이렉트 3번.

- **틀 참조**(`{{ … }}`, 공백 허용): `{{secret.이름}}`(헤더·본문만), `{{state.user_message}}`·`{{state.label}}`·`{{state.confidence}}`·
  `{{state.fields.이름}}`(URL 경로·쿼리, 본문). 그 밖의 모양·닫히지 않은 `{{`는 `invalid_config`. 틀은 한 번에 치환한다(방문자 글에
  든 `{{secret.…}}`은 다시 해석되지 않는다). 없는 값은 빈 글, 참·거짓은 `true`/`false`, 목록·객체는 JSON.
- **URL**: 호스트(포트·사용자 정보 포함)는 **고정 글**이어야 한다 — `https://{{…}}/`, `https://api{{…}}.example.com`,
  `https://api.example.com{{…}}/` 모두 `invalid_config`. 상태 값은 **한 조각**으로 퍼센트 인코딩된다(`/`·`?`·`#`·`&`·`%`·`.`까지) —
  방문자 글로 호스트를 바꾸거나 경로를 거슬러 오르거나(`..`) 쿼리를 끼워 넣지 못한다.
- **헤더**: 고정 글과 `{{secret.이름}}`만(방문자 값 금지). 줄바꿈 금지, `Host`·`Content-Length`·`Transfer-Encoding`·`Connection`·`Upgrade`·`TE`는
  직접 정할 수 없다. 인증 계열 이름이면 값은 참조만(아래 「비밀은 참조만」).
- **본문**: `body_template`의 참조 값은 **JSON 문자열 안쪽으로 이스케이프**돼 들어간다 — `"{{state.user_message}}"`처럼 따옴표 안에 둔다.
  본문이 있고 `Content-Type`을 정하지 않았으면 `application/json`.
- **save**: `{이름: 경로}` — 응답 JSON에서 꺼내 상태 `fields.이름`에 **더한다**(기존 `fields`는 남는다). 조건 노드(`fields.이름`)와 뒤의 HTTP
  도구(`{{state.fields.이름}}`)가 읽는다. 경로는 `$`로 시작해 `.이름`·`[숫자]`·`['이름']`만 잇는다(`$`는 응답 전체, 와일드카드·`..`·필터·음수
  없음). 이름은 `[A-Za-z_][A-Za-z0-9_]{0,63}`. 경로에 값이 없으면 `null`(판단 기록 `missing`). 실패하면 `save`의 이름은 모두 `null`.
- **리다이렉트**: 비밀(헤더·본문)을 실은 요청은 **다른 호스트로의 리다이렉트를 따르지 않는다**(`cross_host_redirect` → `error`) — 보안 계층은
  다른 호스트로 갈 때 `Authorization`·`Proxy-Authorization`·`Cookie`만 떼므로 `X-Api-Key` 같은 헤더나 307 본문의 비밀이 따라가는 것을 막는다.
  비밀이 없는 요청은 허용 목록 안에서 따른다. 같은 호스트 리다이렉트는 비밀이 있어도 따른다.
- **갈래**: 2xx면 `next`. 실패하면 `error`(선택 — 선이 없으면 거기서 턴이 끝난다). 실패는 턴을 깨지 않는다.
- **판단 기록** `record`: `request {method, url}`(치환한 URL — 비밀은 URL에 못 들어간다), `status`(응답이 없으면 `null`), `error`(이유 코드 또는
  `null`), `output`(저장한 값), `missing`, `reason`. 헤더·본문은 기록하지 않는다. `url`·`output`·`reason`은 이 테넌트 비밀(원문·파생값·6자 이상
  조각)로 가린다 — 응답이 비밀을 되돌려 보내도 상태·기록에 남지 않는다.
- **시험 한 턴**도 실제로 부른다(운영과 같은 갈래). 체험 턴 규칙(ADR-0026)은 그대로다.

`record.error` 이유 코드:

| code | 뜻 |
|---|---|
| `host_not_allowed` | 허용 도메인에 없는 호스트(첫 요청이나 리다이렉트 대상). |
| `blocked_address` | 해석한 IP가 사설·루프백·링크로컬·메타데이터 등(DNS 리바인딩 포함 — 홉마다 다시 해석). |
| `dns_failed` / `connect_failed` / `tls_failed` | 이름 해석·연결·TLS 실패. |
| `timeout` | 전체 5초(해석·연결·리다이렉트 포함) 초과. |
| `too_large` | 응답 본문 256KB 초과. |
| `too_many_redirects` / `bad_redirect` | 리다이렉트 3번 초과 / `Location` 없음·틀림. |
| `cross_host_redirect` | 비밀을 실은 요청이 다른 호스트로 리다이렉트됨. |
| `invalid_url` / `not_https` / `ip_literal` / `userinfo` / `non_default_port` / `invalid_request` / `bad_response` | URL·요청·응답 모양이 틀림(보안 계층). |
| `http_error` | 응답이 2xx가 아님(`status`에 코드). |
| `invalid_json` | `save`가 있는데 응답이 JSON이 아님. |
| `unknown_secret` | 참조한 비밀이 실행 때 없음(저장 뒤 지워짐 — 저장 검증은 `unknown_secret` 노드 오류로 막는다). |
| `internal_error` | 그 밖의 예상 못 한 실패(예외 이름만). |

### 비밀은 참조만

HTTP 도구의 인증 계열 헤더(이름에 `authorization`·`cookie`·`token`·`secret`·`api-key`·`password`가 들어감) 값에는
`{{secret.이름}}` 참조만 넣는다. 앞에 스킴 한 단어는 된다(`Bearer {{secret.inquiry_api_key}}`). 원문 키를 넣으면
`invalid_config`. 치환은 실행 때 서버에서만 한다(ADR-0029). url에는 비밀 참조를 넣을 수 없다.
참조한 이름이 테넌트 비밀(설정 → 비밀, `PUT /api/tenant/secrets/{name}`)에 없으면 `unknown_secret`(노드 오류) — 스키마가 아니라
테넌트별 검증(`services.validate_for_tenant`)이 낸다.

## 검증

`validate_flow(data)`는 오류 목록을 돌려준다(빈 목록이면 유효). 오류마다 `node_id`(흐름 전체 오류면 `null`),
`code`, 사람이 읽을 `message`가 있다. 편집기는 `node_id`로 노드에 표시한다. `invalid_config` 의 `message` 는 `경로: 이유; 경로: 이유` 꼴(경로 예 `branches.0.when`)이라 편집기가 해당 필드 아래에 보인다(경로 없는 이유는 노드 전체에). 폼의 틀은 `GET /api/tenant/flows/node-schema/`(종류별 config JSON 스키마, MCP `get_flow_schema`와 같은 모델)에서 뽑는다.

| code | 뜻 |
|---|---|
| `invalid_shape` | 문서 모양이 틀림(노드 목록이 없음 등). 흐름 전체 오류 하나로 나온다. |
| `start_count` | 시작 노드가 정확히 하나가 아님. |
| `unknown_kind` | 알 수 없는 노드 종류. |
| `invalid_config` | 종류별 config가 틀림(필드 이름과 이유가 메시지에). |
| `duplicate_node_id` / `duplicate_code` / `invalid_code` | 노드 id·N-코드 중복, N-코드 꼴이 틀림. |
| `dangling_edge` | 선이 없는 노드를 가리킴. |
| `edge_into_start` | 시작 노드로 들어오는 선. |
| `unknown_handle` | 그 노드에 없는 갈래에서 나가는 선(사람 넘김에서 나가는 선 포함). |
| `duplicate_handle` | 한 갈래에서 선이 둘 이상. |
| `empty_branch` | 조건 노드의 갈래에 선이 없음. |
| `unreachable_node` | 시작 노드에서 갈 수 없는 노드. |
| `cycle_without_turn_boundary` | 방문자 답을 기다리는 노드를 지나지 않는 순환. |

### 순환 규칙

순환은 허용한다. 단, **순환마다 `await_reply: true`인 AI 답변을 하나 이상 지나야 한다.** 실행기는 그 노드에서 답을
보낸 뒤 턴을 끝내고, 다음 턴은 그 노드의 나가는 선에서 이어 간다. 그래서 「되묻기 → 다시 분류」 같은 순환은 턴마다
한 바퀴씩만 돈다. 실행기는 안전장치로 한 턴에 노드 `MAX_STEPS_PER_TURN`(50)개까지만 실행한다.

## Flow Version과 해시

- Flow Version은 바뀌지 않는다. 만들 때 검증하고(무효면 만들지 않음) 해시를 계산한다. 저장 뒤 `save()`나
  `QuerySet.update()`로 고치려 하면 `ImmutableFlowVersionError`.
- 해시는 정규화 JSON(키 정렬, 공백 없는 구분자, UTF-8)의 SHA-256 16진수. 키 순서·들여쓰기가 달라도 같은 흐름은 같은 해시.
- 초안은 작업본이라 무효인 JSON도 저장된다(저장·패치 결과에 검증 오류 목록이 온다). 「버전 저장」이 초안을 새 버전으로 굳힐 때
  검증되고, 배포는 거기에 더해 실행기에 핸들러가 있는 노드만으로 된 버전만 받는다(`no_handler` 오류). 편집 규칙은
  `apps/flows/services.py`(공용 서비스 층 — MCP·흐름 API·편집기가 함께 쓴다).
- 테넌트당 Flow 하나를 `is_public`으로 정하면 위젯이 그 Flow의 배포 중 버전으로 돈다. 새 ChatSession은 만들어질 때 그
  버전에 고정되고(`flow_version`), 이후 배포가 바뀌어도 그대로다. 공개 흐름이나 배포 중 버전이 없으면 비어 있다.
- 다른 테넌트의 흐름은 `Flow.objects.for_tenant(tenant_id)`·`FlowVersion.objects.for_tenant(tenant_id)`에 보이지 않고,
  세션을 다른 테넌트의 버전에 고정하려 하면 거부된다.

## 예제 ① Sandcorp 홈 상담 에이전트

의도 분류 → (프로젝트 문의면) 요구사항이 다 모였나 → 모자라면 **요구사항 되묻기**(답을 기다린 뒤 다시 의도 분류로) /
다 모였으면 사례 찾기 → 범위·기간 초안 → 가드레일 → 문의 접수(HTTP). 사람을 원하거나 의도가 불확실하면 사람 넘김.

```
N-01 시작 → N-02 의도 분류 → N-03 의도에 따라
  inquiry → N-04 요구사항이 다 모였나
              complete → N-06 사례 찾기 → N-07 범위·기간 초안 → N-08 가드레일 → N-09 문의 접수(HTTP)
              else     → N-05 요구사항 되묻기(await_reply) ──(다음 턴)──→ N-02 의도 분류
  human   → N-10 사람 넘김
  else    → N-11 일반 답변
```

전문은 `backend/apps/flows/examples/sandcorp_home.json`. 핵심 부분:

```json
{"id": "classify", "kind": "ai_classify", "code": "N-02", "name": "의도 분류",
 "config": {"instructions": "방문자 의도를 분류하고, 요구사항이 다 모였는지 함께 판단한다.",
            "labels": ["project_inquiry", "human_request", "other"],
            "fields": [{"name": "requirements_complete", "type": "boolean", "description": "목적·규모·기한이 모두 나왔으면 true"}]}},
{"id": "route", "kind": "condition", "code": "N-03", "name": "의도에 따라",
 "config": {"branches": [{"name": "inquiry", "when": "label == 'project_inquiry'"},
                         {"name": "human", "when": "label == 'human_request' or confidence < 0.5"}]}},
{"id": "ask", "kind": "ai_answer", "code": "N-05", "name": "요구사항 되묻기",
 "config": {"instructions": "빠진 요구사항(목적·규모·기한) 중 하나만 짧게 되묻는다.", "await_reply": true}},
{"id": "submit", "kind": "http_tool", "code": "N-09", "name": "문의 접수",
 "config": {"method": "POST", "url": "https://api.sandcorp.co.kr/inquiries",
            "headers": {"Authorization": "Bearer {{secret.inquiry_api_key}}"},
            "body_template": "{\"summary\": \"{{state.user_message}}\", \"label\": \"{{state.label}}\"}"}}
```

```json
"edges": [
  {"from": "route", "to": "enough", "handle": "inquiry"},
  {"from": "route", "to": "handoff", "handle": "human"},
  {"from": "route", "to": "general", "handle": "else"},
  {"from": "enough", "to": "cases", "handle": "complete"},
  {"from": "enough", "to": "ask", "handle": "else"},
  {"from": "ask", "to": "classify"}
]
```
