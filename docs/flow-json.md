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

종류마다 config 모양이 다르다. 모르는 필드는 거부한다. (HTTP 도구는 아직 모양만 있고 실행 핸들러가 없다 — M3.)

| kind | 이름 | config | 나가는 갈래 |
|---|---|---|---|
| `start` | 시작 | `{}` | `next` |
| `ai_answer` | AI 답변 | `instructions: str = ""`, `await_reply: bool = false` | `next`, `handoff`(선택 — AI가 사람 넘김이 필요하다고 판단할 때만 이 선으로) |
| `condition` | 조건 | `branches: [{name, when}]`(1개 이상, 이름 유일, `else`·`next` 예약). `when`은 아래 「when 식」 | 각 `name` + `else` — **모두 선 필수** |
| `handoff` | 사람 넘김 | `reason: str = ""` | 없음 |
| `knowledge_search` | 지식 검색 | `top_k: int = 5`(1~20) | `next` |
| `ai_classify` | AI 분류·추출 | `instructions: str = ""`, `labels: [str] = []`(분류), `fields: [{name, description = "", type = "string"\|"number"\|"boolean"}] = []`(추출) — **둘 중 하나는 있어야** | `next` |
| `guardrail` | 가드레일 | `rules: ["banned_words" \| "pii" \| "price_promise"]`(1개 이상, 중복 불가), `action: "block" \| "rewrite" \| "mask" = "rewrite"`, `target: "output" \| "input" = "output"`, `banned_words: [str] = []`(`banned_words` 규칙이면 1개 이상), `block_message: str`(기본 「죄송합니다. 해당 내용은 안내해 드릴 수 없습니다.」). `price_promise`와 `mask`는 함께 못 쓴다. 자세한 동작은 아래 「가드레일」 | `next` |
| `http_tool` | HTTP 도구 | `method`(GET·POST·PUT·PATCH·DELETE), `url`(https만, user@host 금지), `headers: {str: str}`, `body_template: str \| null` | `next` |

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

### 비밀은 참조만

HTTP 도구의 인증 계열 헤더(이름에 `authorization`·`cookie`·`token`·`secret`·`api-key`·`password`가 들어감) 값에는
`{{secret.이름}}` 참조만 넣는다. 앞에 스킴 한 단어는 된다(`Bearer {{secret.inquiry_api_key}}`). 원문 키를 넣으면
`invalid_config`. 치환은 실행 때 서버에서만 한다(ADR-0029). url에는 비밀 참조를 넣을 수 없다.

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
            "headers": {"Authorization": "Bearer {{secret.inquiry_api_key}}"}}}
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
