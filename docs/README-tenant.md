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

## 4. 위젯 삽입 — 서버사이드 구현

챗봇 위젯을 삽입하려면 **서버에서 EmbedToken을 발급**해야 합니다. TENANT_KEY는 절대 프론트엔드에 노출하지 마세요.

### EmbedToken 발급 (서버사이드)

```bash
POST https://your-platform/api/embed/token
Authorization: Bearer {TENANT_KEY}
Content-Type: application/json

{
  "visitor_id": "user-123",          # 자사 시스템의 사용자 ID
  "visitor_context": {               # 선택: 챗봇에게 전달할 방문자 정보
    "name": "홍길동",
    "plan": "premium",
    "language": "ko"
  }
}
```

응답:
```json
{
  "embed_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### 위젯 삽입 (프론트엔드)

발급받은 `embed_token`을 iframe src에 담아 페이지에 삽입합니다.

```html
<!-- 서버에서 생성한 embed_token을 사용 -->
<iframe
  src="https://your-platform/embed/?token={embed_token}"
  width="400"
  height="600"
  style="border: none; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1);"
  allow="microphone"
></iframe>
```

### 서버사이드 구현 예시

**Node.js / Express**
```js
app.get('/chat-token', async (req, res) => {
  const response = await fetch('https://your-platform/api/embed/token', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${process.env.TENANT_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      visitor_id: req.user.id,
      visitor_context: { name: req.user.name, plan: req.user.plan },
    }),
  })
  const { embed_token } = await response.json()
  res.json({ embed_token })
})
```

**Python / Django**
```python
import requests

def get_embed_token(visitor_id, visitor_context=None):
    response = requests.post(
        'https://your-platform/api/embed/token',
        headers={'Authorization': f'Bearer {settings.TENANT_KEY}'},
        json={'visitor_id': visitor_id, 'visitor_context': visitor_context or {}},
    )
    return response.json()['embed_token']
```

### 토큰 갱신

EmbedToken은 기본 **5분** 후 만료됩니다. 페이지 로드 시마다 새로 발급하거나, 만료 전 갱신 로직을 구현하세요.

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
| `/api/embed/token` | POST | EmbedToken 발급 |
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
- EmbedToken은 짧은 유효기간(기본 5분)을 유지하세요.
- `visitor_id`는 자사 인증 시스템과 연동된 실제 사용자 식별자를 사용하세요.
