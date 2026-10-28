"""기본 시험의 임베딩은 결정적 가짜(fake-llm ``/v1/embeddings``)다(#260).

실제 HTTP로 부르는 외부 경계만 가짜이고 우리 코드(``get_embeddings``·사용량 기록)는 실제다.
실제 bge-m3는 ``test_real_embedding.py``(``real_embedding`` 마커)가 맡는다.
"""
import math

import pytest

from apps.rag.ingesters import get_embeddings


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_default_tests_do_not_point_at_a_real_ollama(settings):
    assert "ollama" not in settings.OLLAMA_BASE_URL, "기본 시험은 가짜 임베딩(fake-llm)을 가리켜야 한다"


def test_same_text_gives_same_1024_dim_unit_vector():
    a, b = get_embeddings(["푸시 알림 설정 방법", "푸시 알림 설정 방법"])
    assert a == b
    assert len(a) == 1024
    assert math.isclose(math.sqrt(_dot(a, a)), 1.0, rel_tol=1e-6)


def test_empty_text_still_gets_a_normalized_vector():
    (v,) = get_embeddings([""])
    assert len(v) == 1024 and math.isclose(_dot(v, v), 1.0, rel_tol=1e-6)


def test_overlapping_words_are_closer_than_unrelated_text():
    q, near, far = get_embeddings(["푸시 알림 설정 방법", "푸시 알림 설정", "결제 환불 규정"])
    assert _dot(q, near) > 0.5 > _dot(q, far)


@pytest.mark.django_db
def test_embedding_usage_is_recorded_like_the_real_path():
    """응답 usage가 있어야 ``record_embedding_usage``가 행을 남긴다 — 가짜도 실제처럼 usage를 준다."""
    import uuid

    from apps.usage.context import set_usage_context
    from apps.usage.models import TokenUsage

    tid = uuid.uuid4()
    set_usage_context(tid, "embedding")
    get_embeddings(["푸시 알림 설정 방법"])
    row = TokenUsage.objects.get(tenant_id=tid, call_type="embedding")
    assert row.total_tokens > 0 and row.request_count == 1
