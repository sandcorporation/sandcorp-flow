"""모델 단가 표 ↔ LiteLLM 단가 파일 일일 대조(#236). 표는 읽기만 한다 — 자동으로 고치지 않는다.

바깥 호출은 ``LITELLM_URL`` 한 곳(시간 제한)과, 설정돼 있으면 경보 웹훅뿐이다. 시험은 ``TRANSPORT``를 바꿔
이 HTTP 경계만 녹화 응답으로 대신한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import httpx
from django.conf import settings
from django.utils import timezone

from apps.usage import pricing
from apps.usage.models import TokenUsage

logger = logging.getLogger(__name__)

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
TIMEOUT_SECONDS = 10.0
TRANSPORT: httpx.BaseTransport | None = None  # 시험이 HTTP 경계를 바꿔 끼우는 자리
_MTOK = Decimal(1_000_000)


class FetchError(Exception):
    """파일을 받지 못했거나(시간 초과·HTTP 오류) 형식이 바뀌었다."""


@dataclass(frozen=True)
class Mismatch:
    model: str
    field: str  # input | output
    ours: Decimal  # USD / 100만 토큰
    theirs: Decimal
    ratio: Decimal  # 상대 차이(큰 쪽 기준)


@dataclass
class Report:
    checked: int = 0
    fetch_error: str | None = None
    unpriced_recent: list[str] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)
    unverifiable: list[str] = field(default_factory=list)
    tiered: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_problem(self) -> bool:
        return bool(self.fetch_error or self.unpriced_recent or self.mismatches or self.unverifiable)

    @property
    def exit_code(self) -> int:
        return 2 if self.fetch_error else 1 if self.has_problem else 0

    def lines(self) -> list[str]:
        out: list[str] = []
        if self.fetch_error:
            out.append(f"대조 실패: {self.fetch_error}")
        if self.unpriced_recent:
            out.append("표에 없는 모델이 최근 쓰였다: " + ", ".join(self.unpriced_recent))
        for m in self.mismatches:
            out.append(f"단가 차이 {m.model} {m.field}: 표 {m.ours} / LiteLLM {m.theirs} (100만 토큰당 USD, {m.ratio:.1%})")
        if self.unverifiable:
            out.append("대조 불가(LiteLLM에 키 없음): " + ", ".join(self.unverifiable))
        for model, keys in self.tiered.items():
            out.append(f"구간 단가 있음(대조 제외) {model}: " + ", ".join(keys))
        return out

    def summary(self) -> str:
        if self.fetch_error:
            return f"모델 단가 대조 실패 — {self.fetch_error}"
        if not self.has_problem:
            return f"모델 단가 대조: 문제 없음 (표 {self.checked}개 모델)"
        return (
            f"모델 단가 대조: 문제 있음 (표 {self.checked}개 모델 — 표에 없는 모델 {len(self.unpriced_recent)}, "
            f"단가 차이 {len(self.mismatches)}, 대조 불가 {len(self.unverifiable)})"
        )


def fetch_litellm() -> dict:
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS) as client:
            response = client.get(LITELLM_URL)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FetchError(f"{LITELLM_URL} 를 받지 못했다: {type(exc).__name__} {exc}".strip()) from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise FetchError("응답이 JSON이 아니다(형식 바뀜)") from exc
    if not isinstance(data, dict):
        raise FetchError("응답이 JSON 객체가 아니다(형식 바뀜)")
    return data


def _per_mtok(entry: dict, key: str, model: str) -> Decimal:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FetchError(f"형식 바뀜: {model} 에 {key} 숫자가 없다")
    try:
        return Decimal(str(value)) * _MTOK
    except InvalidOperation as exc:  # nan 등
        raise FetchError(f"형식 바뀜: {model} 의 {key} 값이 숫자가 아니다") from exc


def _recent_unpriced(days: int) -> list[str]:
    since = timezone.localdate() - timedelta(days=days)
    names = TokenUsage.objects.filter(date__gte=since).values_list("model", flat=True).distinct()
    return sorted(n for n in names if pricing.canonical_model(n) is None)


def compare(table: dict[str, tuple[Decimal, Decimal]], data: dict, tolerance: Decimal) -> Report:
    report = Report(checked=len(table))
    for model, ours in table.items():
        entry = data.get(model)
        if entry is None:
            report.unverifiable.append(model)
            continue
        if not isinstance(entry, dict):
            raise FetchError(f"형식 바뀜: {model} 항목이 객체가 아니다")
        theirs = (_per_mtok(entry, "input_cost_per_token", model), _per_mtok(entry, "output_cost_per_token", model))
        for label, a, b in (("input", ours[0], theirs[0]), ("output", ours[1], theirs[1])):
            top = max(a, b)
            ratio = Decimal(0) if top == 0 else abs(a - b) / top
            if ratio > tolerance:
                report.mismatches.append(Mismatch(model, label, a, b, ratio))
        tier_keys = sorted(k for k in entry if "_above_" in k and k.startswith(("input_cost", "output_cost")))
        if tier_keys:
            report.tiered[model] = tier_keys
    return report


def run_check() -> Report:
    unpriced = _recent_unpriced(settings.PRICE_CHECK_RECENT_DAYS)
    try:
        report = compare(pricing._table(), fetch_litellm(), settings.PRICE_CHECK_TOLERANCE)
    except FetchError as exc:
        report = Report(checked=len(pricing._table()), fetch_error=str(exc))
    report.unpriced_recent = unpriced
    return report


def _webhook_body(kind: str, report: Report) -> dict:
    text = "\n".join([report.summary(), *report.lines()])[:1900]
    if kind == "discord":
        return {"content": text}
    if kind == "slack":
        return {"text": text}
    return {"summary": report.summary(), "problems": report.lines(), "checked_at": timezone.now().isoformat()}


def send_alert(report: Report) -> None:
    """설정된 웹훅으로 문제를 보낸다. 실패는 로그만 — 명령의 종료 코드는 대조 결과가 정한다."""
    url = settings.PRICE_ALERT_WEBHOOK_URL
    if not url or not report.has_problem:
        return
    kind = (settings.PRICE_ALERT_WEBHOOK_TYPE or "generic").lower()
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS) as client:
            client.post(url, json=_webhook_body(kind, report)).raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("단가 경보 웹훅 전송 실패: %s", type(exc).__name__)
