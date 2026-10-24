"""웹 소스 인제스션 보조 (deep module).

명시적 URL을 fetch하고(httpx) 메인 콘텐츠를 추출한다(trafilatura, 보일러플레이트 제거).
fetch와 추출을 분리해 추출은 fixture HTML로 격리 테스트된다(재귀 크롤 아님).
"""
import re


def fetch_html(url: str, timeout: float = 20.0) -> str:
    import httpx

    resp = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; sandcorp-flow-ingester)"},
    )
    resp.raise_for_status()
    return resp.text


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
