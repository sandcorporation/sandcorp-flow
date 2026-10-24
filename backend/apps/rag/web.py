"""웹 소스 인제스션 보조 (deep module).

명시적 URL을 fetch하고 메인 콘텐츠를 추출한다(trafilatura, 보일러플레이트 제거).
fetch와 추출을 분리해 추출은 fixture HTML로 격리 테스트된다(재귀 크롤 아님).

URL은 테넌트가 넣은 값이라 안전 계층(`apps.core.safe_http`, 공용 호스트 정책)으로만 가져온다(#108):
해석한 IP가 하나라도 내부망·사설·메타데이터면 거부, 검사한 IP로만 연결, 리다이렉트마다 다시 검사.
"""
import logging
import re

from apps.core.safe_http import (
    Limits,
    SafeHttpError,
    SafeHttpErrorCode,
    SafeResponse,
    tenant_url_client,
    user_message,
)

logger = logging.getLogger(__name__)

WEB_SCHEMES = ("http", "https")
# 웹 문서는 HTTP 도구(256KB)보다 크다 — 보통 문서 페이지 HTML은 수백 KB~1MB대라 5MB면 잘리지 않고,
# 그래도 상한을 둬 메모리·추출 시간을 묶는다. 리다이렉트는 http→https→www 같은 사슬을 위해 5번.
WEB_LIMITS = Limits(total_timeout=20.0, max_body_bytes=5 * 1024 * 1024, max_redirects=5)
_TRANSIENT = {SafeHttpErrorCode.TIMEOUT, SafeHttpErrorCode.CONNECT_FAILED, SafeHttpErrorCode.DNS_FAILED}


class WebFetchError(Exception):
    """사람이 읽을 사유(문서 목록의 failed 사유로 그대로 보인다). 해석한 IP는 담지 않는다."""


class WebFetchTransientError(WebFetchError):
    """시간 초과·연결 실패·DNS 실패 — 인제스션 태스크가 다시 시도한다."""


def fetch_html(url: str) -> str:
    client = tenant_url_client(WEB_SCHEMES, WEB_LIMITS)
    try:
        resp = client.request(
            "GET", url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; sandcorp-flow-ingester)",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            },
        )
    except SafeHttpError as err:
        logger.warning("web document fetch refused: %s", err)  # 호스트·해석 IP는 로그에만
        cls = WebFetchTransientError if err.code in _TRANSIENT else WebFetchError
        raise cls(f"이 주소는 가져올 수 없습니다: {user_message(err, WEB_SCHEMES)}") from err
    if not 200 <= resp.status < 300:
        raise WebFetchError(f"이 주소는 가져올 수 없습니다: 웹 페이지가 HTTP {resp.status}로 응답했습니다")
    return _decode(resp)


_CHARSET_RE = re.compile(r"charset=[\"']?([\w.-]+)", re.IGNORECASE)


def _decode(resp: SafeResponse) -> str:
    m = _CHARSET_RE.search(resp.header("content-type") or "")
    if m:
        try:
            return resp.body.decode(m.group(1), errors="replace")
        except LookupError:
            pass
    head = resp.body[:2048].decode("ascii", errors="ignore")
    m = _CHARSET_RE.search(head)  # <meta charset="..."> / http-equiv
    if m:
        try:
            return resp.body.decode(m.group(1), errors="replace")
        except LookupError:
            pass
    return resp.body.decode("utf-8", errors="replace")


# trafilatura의 콘텐츠 밀도 휴리스틱이 작은 페이지에서 흔들리므로, 명백한 보일러플레이트
# 태그를 선제 제거한 뒤 본문을 추출한다.
_BOILERPLATE_RE = re.compile(
    r"<(nav|header|footer|aside|script|style)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)


def extract_main_content(html: str) -> str:
    import trafilatura

    cleaned = _BOILERPLATE_RE.sub(" ", html)
    text = trafilatura.extract(
        cleaned, include_comments=False, include_tables=True, favor_precision=True
    )
    return text or ""


def extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""
