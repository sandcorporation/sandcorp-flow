// 노드 설정 폼에서 사람이 읽는 말. 필드의 존재·선택지·필수·범위는 백엔드 스키마(nodeForm.ts)가 정하고, 여기는 라벨·도움말만 둔다.
// 키: `종류.필드` (객체 배열의 한 줄 안은 `종류.필드.하위필드`). 라벨이 없는 필드는 스키마의 필드 이름을 그대로 쓴다.
export type FieldText = { label: string; keyLabel?: string; valueLabel?: string; keyPlaceholder?: string; valuePlaceholder?: string; help?: string; options?: Record<string, string>; add?: string; placeholder?: string }

export const FIELD_TEXT: Record<string, FieldText> = {
  'ai_answer.instructions': { label: '답변 지침', help: '이 단계에서 AI가 어떻게 답할지. 비우면 기본 지침을 씁니다.' },
  'ai_answer.await_reply': { label: '답한 뒤 방문자의 답을 기다립니다(되묻기)', help: '켜면 답한 뒤 턴이 끝나고, 다음 턴은 이 노드의 나가는 선에서 이어집니다.' },

  'condition.branches': { label: '갈래', add: '갈래 추가', help: '위에서부터 처음 참인 갈래로 갑니다. 어느 것도 아니면 「else」 갈래입니다. 갈래마다 캔버스에서 선을 이어야 합니다.' },
  'condition.branches.name': { label: '갈래 이름', help: '`else`·`next` 는 예약어' },
  'condition.branches.when': { label: '조건식', placeholder: "label == 'refund' or confidence < 0.5" },

  'ai_classify.instructions': { label: '분류·추출 지침', help: '분류 기준이나 뽑을 값에 대한 설명.' },
  'ai_classify.labels': { label: '분류 라벨', help: '한 줄에 하나. 결과는 조건식의 label 로 읽습니다. 라벨이나 추출 필드 중 하나는 있어야 합니다.' },
  'ai_classify.fields': { label: '추출 필드', add: '추출 필드 추가', help: '대화에서 뽑을 값. 못 찾으면 null 이며 조건식에서 fields.이름 으로 읽습니다.' },
  'ai_classify.fields.name': { label: '필드 이름', help: '영문·숫자·밑줄' },
  'ai_classify.fields.description': { label: '설명' },
  'ai_classify.fields.type': { label: '형식', options: { string: '글', number: '숫자', boolean: '참/거짓' } },

  'knowledge_search.top_k': { label: '가져올 근거 수', help: '1~20. 많을수록 넓게 찾지만 답이 길어질 수 있습니다.' },

  'handoff.reason': { label: '넘기는 사유', help: '상담원이 보는 한 문장. 방문자에게는 보이지 않습니다.' },

  'guardrail.rules': {
    label: '검사 종류',
    help: '하나 이상 고릅니다.',
    options: { banned_words: '금지어', pii: '개인정보(전화·주민번호·카드·이메일)', price_promise: '가격 약속' },
  },
  'guardrail.action': {
    label: '처리',
    help: '걸렸을 때. 가리기는 금지어·개인정보에만 쓸 수 있습니다.',
    options: { block: '막기(막을 때 문구로 바꿈)', rewrite: '다시 쓰기(AI가 고쳐 씀)', mask: '가리기(걸린 부분만)' },
  },
  'guardrail.target': { label: '대상', options: { output: '나가는 말(직전 AI 답변)', input: '들어오는 말(방문자 입력)' } },
  'guardrail.banned_words': { label: '금지어 목록', help: '한 줄에 하나. 「금지어」 검사를 고르면 필요합니다.' },
  'guardrail.block_message': { label: '막을 때 문구', help: '비우면 기본 문구를 씁니다.' },

  'http_tool.method': { label: '메서드' },
  'http_tool.url': { label: '주소(https)', help: 'https:// 로 시작합니다. 호스트(도메인·포트)는 고정 글이어야 하고, {{state.…}} 는 경로·쿼리에만 쓸 수 있습니다. 주소에는 비밀 참조를 넣을 수 없습니다 — 비밀은 헤더에 두세요.', placeholder: 'https://api.example.com/orders' },
  'http_tool.headers': { label: '헤더', add: '헤더 추가', help: '인증 계열 헤더(Authorization·Cookie·토큰·키·비밀번호)는 원문 대신 {{secret.이름}} 참조만 씁니다.' },
  'http_tool.body_template': { label: '본문 틀', help: '비우면 본문 없이 보냅니다. 쓸 수 있는 참조: {{state.user_message}} · {{state.label}} · {{state.confidence}} · {{state.fields.이름}} · {{secret.이름}}. 값은 JSON 문자열 안쪽으로 들어가니 "{{state.user_message}}" 처럼 따옴표 안에 씁니다.' },
  'http_tool.save': {
    label: '응답→상태', add: '저장할 값 추가', keyLabel: '상태 이름', valueLabel: '응답 경로', keyPlaceholder: 'order_status', valuePlaceholder: '$.data.status',
    help: '응답 JSON의 값을 상태 fields.이름 에 저장합니다. 경로는 $ 로 시작하고 .이름 · [숫자] · [\'이름\'] 만 씁니다. 조건 노드와 다음 HTTP 도구가 읽습니다.',
  },
}

/** 종류별로 폼 위에 붙는 안내. */
export const KIND_NOTE: Record<string, string> = {
  http_tool: '2xx 응답이면 next 갈래로, 차단·시간 초과·크기 초과·HTTP 오류 등 실패면 error 갈래로 갑니다. error 갈래에 선이 없으면 턴이 거기서 끝납니다.',
  start: '시작 노드는 설정할 항목이 없습니다. 방문자가 말을 걸면 여기서 출발합니다.',
}

/** 조건식에서 읽을 수 있는 상태 키(백엔드 expr.SCOPE_NAMES). */
export const WHEN_KEYS: { name: string; help: string }[] = [
  { name: 'label', help: '분류 노드가 고른 라벨' },
  { name: 'confidence', help: '확신도 0~1' },
  { name: 'fields.이름', help: '추출한 값(못 뽑으면 null)' },
  { name: 'user_message', help: '방문자의 마지막 말' },
]

export const GUARDRAIL_WARNING = '가드레일은 방문자에게 나가는 말을 바꾸거나 막을 수 있습니다. 처리와 대상을 확인하고, 저장 전에 시험 대화로 살펴보세요.'

export const SENSITIVE_HEADER_RE = /authorization|cookie|token|secret|api[-_]?key|password/i
export const SECRET_REF_RE = /\{\{\s*secret\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/
/** 백엔드 HttpToolConfig 와 같은 판정: 인증 계열 헤더 값은 참조(앞에 스킴 한 단어 허용)만. */
export function headerNeedsSecretRef(name: string, value: string): boolean {
  if (!SENSITIVE_HEADER_RE.test(name)) return false
  const rest = value.replace(new RegExp(SECRET_REF_RE.source, 'g'), '').trim()
  return !SECRET_REF_RE.test(value) || (rest !== '' && !/^[A-Za-z]+$/.test(rest))
}

/** 시험 대화·실제 방문자 모두 이 API를 실제로 부른다. */
export const HTTP_LIVE_NOTICE = '시험 대화도 이 API를 실제로 호출합니다(POST면 실제로 접수됨).'
