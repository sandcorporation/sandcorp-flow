from django.db import models


class TokenUsage(models.Model):
    """테넌트별 토큰 사용량 일 버킷 롤업(PRD-langfuse-token-tracking).

    호출 상세는 Langfuse가 보관하고, 이 테이블은 인앱 사용량 뷰용 빠른 집계만 담는다.
    (tenant_id, call_type, model, date) 키로 record_usage가 원자적 upsert 증분한다.
    """

    CALL_CHAT = "chat"
    CALL_EXTRACTION = "extraction"
    CALL_EMBEDDING = "embedding"
    CALL_OCR = "ocr"

    tenant_id = models.UUIDField()
    call_type = models.CharField(max_length=20)
    model = models.CharField(max_length=120)
    date = models.DateField()
    input_tokens = models.BigIntegerField(default=0)
    output_tokens = models.BigIntegerField(default=0)
    total_tokens = models.BigIntegerField(default=0)
    request_count = models.IntegerField(default=0)
    # 단가 표(pricing.py)로 계산한 USD 합계. 표에 없는 모델의 호출은 더하지 않는다(비용 모름 ≠ 0원).
    cost_usd = models.DecimalField(max_digits=14, decimal_places=6, default=0)

    class Meta:
        db_table = "token_usage"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "call_type", "model", "date"],
                name="token_usage_unique_bucket",
            )
        ]
        indexes = [models.Index(fields=["tenant_id", "date"])]
