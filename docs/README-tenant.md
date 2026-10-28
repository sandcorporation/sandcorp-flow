# Sandcorp Flow — Tenant Guide

Tenant(고객사)는 Sandcorp Flow 플랫폼을 이용해 자사 웹사이트에 AI 챗봇을 삽입하고, 응대 메뉴얼 업로드·챗봇 설정·방문자 메모리 관리를 할 수 있습니다.

## 시작하기 전에

Operator로부터 다음을 받아야 합니다:

- **TENANT_KEY** — 서버사이드 전용 비밀 키 (브라우저에 절대 노출 금지)
- **플랫폼 URL** — 예: `https://chat.example.com`

## 1. 어드민 UI 로그인

`https://your-platform/admin-ui/` 에 접속 → TENANT_KEY 입력 → 로그인

관리할 수 있는 항목:

| 탭 | 기능 |
|----|------|
| 설정 | LLM 모델 선택, System Prompt 작성 |
| 문서 | 지식 베이스 파일(PDF·TXT) 업로드·삭제 |
| 메모리 | Visitor별 기억 조회·수정·삭제 |

## 2. 챗봇 설정

**설정 탭**에서:

- **LLM 모델**: 드롭다운에서 선택하거나 직접 입력 (OpenRouter 모델 ID)
- **System Prompt**: 챗봇 역할과 응대 방식을 정의하는 지침

```
예시 System Prompt:
당신은 ABC 쇼핑몰의 친절한 고객 상담 AI입니다.
재고 문의, 배송 조회, 반품 정책 안내를 전문적으로 도와드립니다.
모르는 내용은 정직하게 "확인 후 안내드리겠습니다"라고 답하세요.
```

저장 후 즉시 적용됩니다.

## 3. 지식 베이스 업로드

**문서 탭**에서 PDF 또는 TXT 파일을 업로드합니다.

- 업로드 직후 상태: `pending` → `processing` → `ready`
- `ready` 상태가 되면 챗봇이 해당 내용을 참조해 답변
- 지원 형식: **PDF**, **TXT**
- 상태는 3초마다 자동 갱신됩니다

```
권장 문서 예시:
- 제품 카탈로그 (PDF)
- FAQ 모음 (TXT)
- 환불·교환 정책 (PDF)
- 서비스 이용 약관 (TXT)
```

## 4. 위젯 삽입 (iframe)

챗봇 위젯은 **공개 주소**를 iframe으로 붙이면 됩니다. 발급할 토큰이 없고 `TENANT_KEY`도 필요 없습니다(옛 EmbedToken 방식은 폐지됐습니다 — [ADR-0011](./adr/0011-public-slug-url-replaces-embed-token.md)). 설정 > 보안 「내 사이트에 붙이기」 카드가 아래 코드를 만들어 줍니다.

### 기본 흐름 주소

```html
<iframe
  src="https://your-platform/chatbot/{slug}/"
  width="400"
  height="600"
  style="border: none; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1);"
  allow="microphone"
></iframe>
```

`{slug}`는 설정에서 정한 조직 주소입니다. 이 주소는 늘 **기본 흐름**이 답합니다. 기본 흐름을 바꾸면 이 주소의 새 대화부터 새 흐름이 답하고, 이미 열린 대화는 끊기지 않습니다.

### 흐름별 주소

흐름마다 바뀌지 않는 `embed_key`(8자)가 있어, 같은 사이트의 다른 쪽에 다른 흐름을 붙일 수 있습니다. 흐름 목록의 「임베드」 패널에서 **임베드 켜기**를 누르면(배포한 버전이 있어야 합니다) 코드가 나옵니다.

```html
<iframe src="https://your-platform/chatbot/{slug}/{embed_key}/" width="400" height="600" style="border: none" allow="microphone"></iframe>
```

흐름마다 대화가 따로입니다. 임베드를 끄거나 흐름을 보관하면 그 주소는 「지금 열려 있지 않은 상담입니다.」를 보입니다(기본 흐름은 끄더라도 자기 키 주소로 열려 있습니다).

### 방문자 식별

로그인한 사용자를 알리려면 주소 끝에 `?visitor_id=<자사 사용자 ID>`를 붙입니다. 본인 확인을 켠 조직은 위조를 막는 확인값(`hash`)도 함께 요구합니다(설정 > 보안 안내 참고).

### 허용 사이트

기본으로는 어느 사이트든 위 iframe을 붙일 수 있습니다. 설정 > 보안 카드의 「허용 사이트」(조직 전체)와 흐름 「임베드」 패널의 허용 사이트(그 흐름의 키 주소만, 비면 조직 목록을 따름)에 `https://example.com`, `https://*.example.com` 같은 출처를 넣으면 그 사이트에서만 붙습니다. 이것은 **붙이기 통제이지 접근 통제가 아닙니다** — 주소를 직접 열거나 API를 부르는 것은 막지 않습니다.

## 5. 방문자 메모리 관리

**메모리 탭**에서 특정 Visitor의 기억 데이터를 관리합니다.

- **Visitor ID 검색**: 자사 시스템의 사용자 ID 입력 후 검색
- **메모리 항목**: LLM이 대화에서 자동 추출한 `key: value` 형식의 사실
  - 예: `이름: 홍길동`, `선호_배송: 새벽배송`, `반품이력: 2회`
- **수정**: 잘못된 정보 직접 교정 가능
- **삭제**: 불필요한 메모리 제거

### 메모리 API (서버사이드에서 직접 접근 시)

```bash
# Visitor 메모리 조회
GET /api/tenant/visitors/{visitor_id}/memory/
Authorization: Bearer {TENANT_KEY}

# 메모리 수정
PATCH /api/tenant/visitors/{visitor_id}/memory/{memory_id}
Authorization: Bearer {TENANT_KEY}
{"key": "이름", "value": "김철수"}

# 메모리 삭제
DELETE /api/tenant/visitors/{visitor_id}/memory/{memory_id}
Authorization: Bearer {TENANT_KEY}
```

## API 레퍼런스

| 엔드포인트 | 메서드 | 설명 |
|------------|--------|------|
| `/api/tenant/flows/` | GET | 흐름 목록(`embed_key`·`embed_enabled`·`embed_path`·`embed_origins`) |
| `/api/tenant/flows/{id}/embed/` | POST | 흐름의 키 주소 임베드 켜기/끄기 |
| `/api/tenant/flows/{id}/default/` | POST | 기본 흐름 바꾸기 |
| `/api/tenant/flows/{id}/embed-origins/` | PUT | 흐름의 허용 사이트 |
| `/api/tenant/config/` | GET | 챗봇 설정 조회 |
| `/api/tenant/config/` | PATCH | 챗봇 설정 변경 |
| `/api/tenant/documents/` | GET | 문서 목록 |
| `/api/tenant/documents/` | POST | 문서 업로드 |
| `/api/tenant/documents/{id}` | DELETE | 문서 삭제 |
| `/api/tenant/visitors/{visitor_id}/memory/` | GET | Visitor 메모리 조회 |
| `/api/tenant/visitors/{visitor_id}/memory/{id}` | PATCH | 메모리 수정 |
| `/api/tenant/visitors/{visitor_id}/memory/{id}` | DELETE | 메모리 삭제 |

전체 API 스펙: `https://your-platform/api/docs`

## 보안 주의사항

- `TENANT_KEY`는 서버사이드에서만 사용하세요. 환경 변수(`process.env.TENANT_KEY`)로 관리하고, 절대 소스코드나 프론트엔드에 하드코딩하지 마세요.
- iframe 주소에는 비밀이 없습니다. 허용 사이트는 붙이기만 통제하니, 대화 내용 보호는 본인 확인과 `visitor_id` 관리로 하세요.
- `visitor_id`는 자사 인증 시스템과 연동된 실제 사용자 식별자를 사용하세요.
