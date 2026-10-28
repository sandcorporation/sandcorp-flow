"""실제 Ollama bge-m3를 부르는 시험(#260). 기본 실행에서는 빠진다 — ``real_embedding`` 마커.

실행: ``TEST_REAL_EMBEDDING=1 bash scripts/test.sh`` (ollama를 띄우고 bge-m3를 받은 뒤 이 마커 시험만 돈다).
가짜 임베딩(``fake_llm``)이 실제 모델과 같은 계약(차원 1024·정규화·usage)을 지키는지 가끔 맞춰 보는 용도다.
"""
import math
import os

import pytest

from apps.agent.providers import EmbeddingProvider
from apps.rag.ingesters import get_embeddings

pytestmark = pytest.mark.real_embedding

REAL_OLLAMA = os.environ.get("REAL_OLLAMA_BASE_URL", "http://ollama-test:11434")


def _provider():
    return EmbeddingProvider(type="custom", base_url=f"{REAL_OLLAMA}/v1", api_key="ollama", model="bge-m3", dim=1024)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def test_real_bge_m3_returns_1024_dim_vectors():
    (v,) = get_embeddings(["hello world"], provider=_provider())
    assert len(v) == 1024
    assert math.isclose(math.sqrt(_dot(v, v)), 1.0, rel_tol=1e-2)


def test_real_bge_m3_puts_related_text_closer():
    q, near, far = get_embeddings(
        ["푸시 알림은 어떻게 켜나요", "앱 설정에서 푸시 알림을 켤 수 있습니다", "환불 규정 안내"], provider=_provider()
    )
    assert _dot(q, near) > _dot(q, far)
