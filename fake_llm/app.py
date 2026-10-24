"""E2E용 결정적 Fake LLM (OpenAI Chat Completions 호환).

실제 추론 없이 규칙 기반으로 응답한다. E2E 스택에서 비결정적인 qwen 대신 사용해
HITL/chat 단언을 결정적으로 만든다. 절대 외부 호출을 하지 않는다.

판정 규칙:
- 마지막 사용자(role=user) 메시지에 인간 상담원 키워드('상담원')가 있으면 needs_hitl=true.
- system 프롬프트는 판정에서 제외(상담원 안내문이 포함될 수 있으므로).

응답 형식:
- 요청 response_format.type == "json_schema" (langchain with_structured_output) →
  스키마 JSON을 message.content 문자열로 반환.
- 그 외(일반 .invoke) → message.content에 카난 텍스트(기본 빈 facts "{}").
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HUMAN_AGENT_KEYWORD = os.environ.get("FAKE_LLM_HITL_KEYWORD", "상담원")
NORMAL_REPLY = os.environ.get(
    "FAKE_LLM_NORMAL_REPLY", "안녕하세요! 무엇을 도와드릴까요?"
)


def _last_user_message(messages):
    texts = [m.get("content", "") for m in messages if m.get("role") == "user"]
    return texts[-1] if texts else ""


_GLOBAL_KEYWORDS = ("공통", "전체", "요약", "전반", "모든 문서")


def build_completion(body: dict) -> dict:
    """OpenAI chat.completions 요청 dict → 응답 dict (결정적)."""
    messages = body.get("messages", []) or []
    last_user = _last_user_message(messages)

    response_format = body.get("response_format") or {}
    is_structured = response_format.get("type") == "json_schema"
    schema_name = ""
    if is_structured:
        schema_name = (response_format.get("json_schema") or {}).get("name", "")

    if schema_name == "GraphExtraction":
        # GraphRAG Entity/관계 추출 — 결정적 그래프
        content = json.dumps(
            {
                "entities": [
                    {"name": "FOOTSWITCH", "type": "feature", "description": "a footswitch"},
                    {"name": "EXPRESSION_PEDAL", "type": "feature", "description": "a pedal"},
                ],
                "relations": [
                    {"source": "FOOTSWITCH", "target": "EXPRESSION_PEDAL", "description": "paired with"},
                ],
            },
            ensure_ascii=False,
        )
    elif schema_name == "SearchRoute":
        scope = "global" if any(k in (last_user or "") for k in _GLOBAL_KEYWORDS) else "local"
        content = json.dumps({"search_scope": scope})
    elif is_structured:
        needs_hitl = HUMAN_AGENT_KEYWORD in (last_user or "")
        content = json.dumps(
            {
                "response": "" if needs_hitl else NORMAL_REPLY,
                "needs_hitl": needs_hitl,
                "hitl_reason": "상담원 요청" if needs_hitl else "",
            },
            ensure_ascii=False,
        )
    else:
        # 일반 completion (예: Visitor Memory 추출 / Community 요약) — 결정적
        content = "{}"

    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model", "fake-llm"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def build_stream_chunks(completion: dict, include_usage: bool = False) -> list[dict]:
    """같은 응답을 OpenAI 스트리밍 청크로 쪼갠다 — 챗 그래프가 stream=true로 부른다(#65: 스트림을 못 받아 위젯이 답을 못 받았다).

    실제 OpenAI처럼 usage 청크는 요청에 stream_options.include_usage가 있을 때만 준다(#86: 늘 주던 대역이 토큰 누락을 숨겼다).
    """
    choice = completion["choices"][0]
    base = {
        "id": completion["id"],
        "object": "chat.completion.chunk",
        "created": completion["created"],
        "model": completion["model"],
    }
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": choice["message"]["content"]}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    if include_usage:
        chunks.append({**base, "choices": [], "usage": completion["usage"]})
    return chunks


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_stream(self, completion, include_usage):
        payload = "".join(
            f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in build_stream_chunks(completion, include_usage)
        ) + "data: [DONE]\n\n"
        data = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            self._send_json(200, {"status": "ok"})
        elif self.path.rstrip("/") == "/v1/models":
            self._send_json(200, {"object": "list", "data": [{"id": "fake-llm", "object": "model"}]})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        if self.path.rstrip("/").endswith("/chat/completions"):
            completion = build_completion(body)
            if body.get("stream"):
                self._send_stream(completion, bool((body.get("stream_options") or {}).get("include_usage")))
            else:
                self._send_json(200, completion)
        else:
            self._send_json(404, {"error": "not found"})


def main():
    port = int(os.environ.get("PORT", "8090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
