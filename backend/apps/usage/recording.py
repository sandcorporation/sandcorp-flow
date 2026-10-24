"""토큰 사용량 기록 (deep module).

record_usage가 TokenUsage 일 버킷 행을 원자적 upsert로 증분한다(ON CONFLICT, GraphStore 패턴).
동시 호출에도 손실 없이 누적된다. 호출 상세는 Langfuse가, 이 집계는 인앱 뷰가 쓴다.
"""
from django.db import connection

from .pricing import cost_usd


def record_usage(tenant_id, call_type: str, model: str,
                 input_tokens: int = 0, output_tokens: int = 0) -> None:
    input_tokens = int(input_tokens or 0)
    output_tokens = int(output_tokens or 0)
    total = input_tokens + output_tokens
    cost = cost_usd(model, input_tokens, output_tokens) or 0  # 모르는 모델은 합계에 더하지 않는다
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO token_usage
                (tenant_id, call_type, model, date,
                 input_tokens, output_tokens, total_tokens, request_count, cost_usd)
            VALUES (%s, %s, %s, CURRENT_DATE, %s, %s, %s, 1, %s)
            ON CONFLICT (tenant_id, call_type, model, date) DO UPDATE SET
                input_tokens  = token_usage.input_tokens  + EXCLUDED.input_tokens,
                output_tokens = token_usage.output_tokens + EXCLUDED.output_tokens,
                total_tokens  = token_usage.total_tokens  + EXCLUDED.total_tokens,
                request_count = token_usage.request_count + 1,
                cost_usd      = token_usage.cost_usd      + EXCLUDED.cost_usd
            """,
            [str(tenant_id), call_type, model, input_tokens, output_tokens, total, cost],
        )


def record_embedding_usage(resp_json: dict, tenant_id, model: str) -> None:
    """임베딩 응답(OpenAI-호환)의 usage를 읽어 embedding 사용량으로 기록한다.

    OpenAI는 usage.total_tokens(또는 prompt_tokens)를 준다. usage가 없으면(일부 ollama/custom)
    조용히 생략한다(추정은 후속). httpx 직접 호출이라 langchain 콜백이 못 잡으므로 수동 경로.
    """
    if not tenant_id:
        return
    usage = (resp_json or {}).get("usage") or {}
    total = usage.get("total_tokens") or usage.get("prompt_tokens") or 0
    if total:
        record_usage(tenant_id, "embedding", model, input_tokens=int(total), output_tokens=0)
