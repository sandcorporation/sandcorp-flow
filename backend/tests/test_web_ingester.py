import io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


# ── Issue 91: WebIngester — 메인 콘텐츠 추출 ──────────────────────────────────

def test_extract_main_content_strips_boilerplate():
    """nav·footer 보일러플레이트는 제거되고 본문만 추출된다."""
    from apps.rag.web import extract_main_content

    html = """<html><head><title>FAQ</title></head><body>
      <nav>홈 메뉴 로그인 회원가입</nav>
      <header>사이트 상단 배너</header>
      <main><article>
        <h1>환불 정책 안내</h1>
        <p>구매 후 30일 이내에는 전액 환불이 가능합니다. 영수증을 지참해 주세요.</p>
      </article></main>
      <footer>저작권 보일러플레이트 콘텐츠 2024</footer>
    </body></html>"""

    text = extract_main_content(html)
    assert "환불" in text and "30일" in text
    assert "보일러플레이트" not in text
    assert "회원가입" not in text


def _serve_html(html: str):
    """고정 HTML을 서빙하는 로컬 HTTP 서버를 띄우고 (url_base, server)를 반환한다."""
    body = html.encode("utf-8")

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


@pytest.mark.django_db
def test_url_document_fetched_and_ingested(client, tenant_admin_token, tenant_with_key):
    """명시적 URL을 추가하면 fetch·본문추출 후 그래프 인제스션까지 도달하고 title이 라벨이 된다."""
    from apps.rag.models import Document

    html = (
        "<html><head><title>환불 FAQ</title></head><body>"
        "<nav>메뉴 로그인</nav>"
        "<main><article><h1>환불 정책</h1>"
        "<p>구매 후 30일 이내에 전액 환불이 가능합니다. 영수증을 지참하세요.</p>"
        "</article></main><footer>copyright 2024</footer></body></html>"
    )
    base, server = _serve_html(html)
    try:
        resp = client.post(
            "/api/tenant/documents/url",
            {"urls": [f"{base}/faq"]},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
        )
        assert resp.status_code == 201
        doc = Document.objects.get(id=resp.json()[0]["id"])
        assert doc.status == Document.STATUS_READY, doc.error_message
        assert doc.source_type == Document.SOURCE_URL
        assert doc.name == "환불 FAQ"  # Document Label = 페이지 title

        # 수동 재-fetch → 다시 ready로 도달
        rf = client.post(
            f"/api/tenant/documents/{doc.id}/refetch",
            HTTP_AUTHORIZATION=f"Bearer {tenant_admin_token}",
        )
        assert rf.status_code == 200
        doc.refresh_from_db()
        assert doc.status == Document.STATUS_READY, doc.error_message
    finally:
        server.shutdown()
