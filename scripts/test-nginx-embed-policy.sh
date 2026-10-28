#!/usr/bin/env bash
# 위젯 주소의 동적 frame-ancestors(#248, ADR-0034) — nginx auth_request 시험.
# 실제 nginx:1.27-alpine 에 운영 conf(nginx.oracle.conf)를 그대로 올리고, 같은 네트워크에 별칭 `api` 로 가짜 정책 서버를 띄운다.
# 가짜는 X-Original-URI 에 따라 204+값 / 500 / 403 / 늦음(3초)으로 답한다. 실패는 모두 "*"(fail-open)여야 하고 /chatbot/ 은 200 이어야 한다.
# 끝에 dev conf(nginx.conf)도 nginx -t 로 문법을 본다.
#   bash scripts/test-nginx-embed-policy.sh
set -uo pipefail
cd "$(dirname "$0")/.."
FIX=$(mktemp -d)
IMG=nginx:1.27-alpine
PY=python:3.12-alpine
PORT=${NGINX_TEST_PORT:-18298}
NET=nginx-embed-policy-test
FAILS=0
cleanup() {
  docker rm -f nginx-embed-test fake-embed-api >/dev/null 2>&1
  docker network rm "$NET" >/dev/null 2>&1
  docker run --rm -v "$FIX:/w" alpine rm -rf /w/widget /w/admin /w/fake >/dev/null 2>&1; rmdir "$FIX" 2>/dev/null
}
trap cleanup EXIT

mkdir -p "$FIX/widget" "$FIX/admin" "$FIX/fake"
page='<!doctype html><html><head><title>t</title></head><body>widget</body></html>'
for f in landing privacy terms index; do echo "$page" > "$FIX/widget/$f.html"; done
echo "$page" > "$FIX/admin/index.html"
cat > "$FIX/fake/api.py" <<'PY'
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        uri = self.headers.get("X-Original-URI", "")
        if self.path != "/_internal/embed-policy":
            return self._send(404)
        if uri == "/chatbot/raw/%ED%95%9C/?a=1&b=2":  # $request_uri 가 그대로(쿼리·퍼센트 인코딩) 넘어왔다
            return self._send(204, "'self' https://raw.example.com")
        if uri.startswith("/chatbot/ok/"):
            return self._send(204, "'self' https://shop.example.com https://*.example.org")
        if uri.startswith("/chatbot/any/"):
            return self._send(204, "*")
        if uri.startswith("/chatbot/err/"):
            return self._send(500)
        if uri.startswith("/chatbot/gone/"):  # 엔드포인트가 없는 옛 api(롤아웃 중)
            return self._send(404)
        if uri.startswith("/chatbot/deny/"):
            return self._send(403)
        if uri.startswith("/chatbot/slow/"):
            time.sleep(3)
            return self._send(204, "'self'")
        return self._send(204, "'self'")

    def _send(self, status, value=None):
        self.send_response(status)
        if value is not None:
            self.send_header("X-Frame-Ancestors", value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass

ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever(poll_interval=0.01)
PY
chmod -R a+rX "$FIX"

docker network create "$NET" >/dev/null
start_api() {
  docker rm -f fake-embed-api >/dev/null 2>&1
  docker run -d --name fake-embed-api --network "$NET" --network-alias api -v "$FIX/fake:/fake:ro" "$PY" python /fake/api.py >/dev/null
}
start_nginx() {
  docker rm -f nginx-embed-test >/dev/null 2>&1
  docker run -d --name nginx-embed-test --network "$NET" -p "127.0.0.1:$PORT:80" \
    -e NGINX_ENVSUBST_FILTER='^(METRICS_|TRY_EMBED_)' -e TRY_EMBED_ORIGINS= -e METRICS_SNIPPET_URL= -e METRICS_SITE_KEY=flow \
    -v "$PWD/backend/nginx/nginx.oracle.conf:/etc/nginx/nginx.conf:ro" \
    -v "$PWD/backend/nginx/flow-metrics.conf.template:/etc/nginx/templates/flow-metrics.conf.template:ro" \
    -v "$FIX/widget:/app/widget:ro" -v "$FIX/admin:/app/admin:ro" "$IMG" >/dev/null
  for _ in $(seq 1 40); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/terms" && return 0; sleep 0.25; done
  docker logs nginx-embed-test 2>&1 | tail -5; FAILS=$((FAILS+1)); echo "FAIL nginx 기동"
}
wait_api() {
  for _ in $(seq 1 40); do
    [ "$(csp /chatbot/ok/)" = "frame-ancestors 'self' https://shop.example.com https://*.example.org" ] && return 0; sleep 0.25
  done
  echo "FAIL 가짜 정책 서버가 뜨지 않음"; FAILS=$((FAILS+1))
}
csp() { curl -sI "http://127.0.0.1:$PORT$1" | tr -d '\r' | awk -F': ' 'tolower($1)=="content-security-policy"{print $2}'; }
code() { curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT$1"; }
expect() { # $1=경로 $2=기대 CSP(정확히) — 상태는 늘 200, 본문은 위젯
  local got st body; got=$(csp "$1"); st=$(code "$1"); body=$(curl -s "http://127.0.0.1:$PORT$1")
  if [ "$got" = "$2" ] && [ "$st" = 200 ] && grep -q widget <<<"$body"; then echo "ok   $1 -> $st $got"
  else echo "FAIL $1: 기대 200 '$2', 실제 $st '$got'"; FAILS=$((FAILS+1)); fi
}

echo "== 정책 서버가 답한다 =="
start_api; start_nginx; wait_api
expect /chatbot/ok/ "frame-ancestors 'self' https://shop.example.com https://*.example.org"
expect /chatbot/ok/abcd1234/ "frame-ancestors 'self' https://shop.example.com https://*.example.org"   # 폴백(@chatbot_index)에도 같은 값
expect /chatbot/ok/index.html "frame-ancestors 'self' https://shop.example.com https://*.example.org"  # 실제 파일도
expect "/chatbot/raw/%ED%95%9C/?a=1&b=2" "frame-ancestors 'self' https://raw.example.com"
expect /chatbot/any/ "frame-ancestors *"
expect /chatbot/nobody/ "frame-ancestors 'self'"

echo "== 정책 서버가 실패하면 열어 둔다(fail-open, 도입 전과 같은 *) =="
expect /chatbot/err/ "frame-ancestors *"
expect /chatbot/gone/ "frame-ancestors *"
expect /chatbot/deny/ "frame-ancestors *"     # 403 을 그대로 두면 auth_request 가 위젯을 막는다
expect /chatbot/slow/x/ "frame-ancestors *"   # proxy_read_timeout 2s
t=$(curl -s -o /dev/null -w '%{time_total}' "http://127.0.0.1:$PORT/chatbot/slow/y/")
awk -v t="$t" 'BEGIN{exit !(t < 3)}' && echo "ok   늦은 정책은 ${t}초에 포기" || { echo "FAIL 늦은 정책 대기 ${t}초"; FAILS=$((FAILS+1)); }

echo "== 정책 엔드포인트는 바깥에서 닿지 않는다 =="
for p in /_internal/embed-policy "/_internal/embed-policy?x=1"; do
  st=$(code "$p"); [ "$st" = 404 ] && echo "ok   $p -> 404" || { echo "FAIL $p: $st"; FAILS=$((FAILS+1)); }
done
st=$(curl -s -o /dev/null -w '%{http_code}' -H 'X-Original-URI: /chatbot/ok/' "http://127.0.0.1:$PORT/_internal/embed-policy")
[ "$st" = 404 ] && echo "ok   머리를 붙여 불러도 404" || { echo "FAIL 머리 붙인 직접 호출: $st"; FAILS=$((FAILS+1)); }

echo "== 다른 위치는 그대로 =="
for p in /embed/ /embed/index.html; do
  got=$(csp "$p"); [ "$got" = "frame-ancestors *" ] && echo "ok   $p -> $got" || { echo "FAIL $p: '$got'"; FAILS=$((FAILS+1)); }
done

echo "== 앱이 없다(재시작·롤아웃 중) =="
docker rm -f fake-embed-api >/dev/null 2>&1
expect /chatbot/ok/ "frame-ancestors *"
expect /chatbot/ok/abcd1234/ "frame-ancestors *"

echo "== dev conf 문법(nginx -t) =="
start_api
out=$(docker run --rm --network "$NET" -v "$PWD/backend/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" "$IMG" nginx -t 2>&1)
grep -q 'test is successful' <<<"$out" && echo "ok   nginx.conf 문법" || { echo "FAIL nginx.conf: $out"; FAILS=$((FAILS+1)); }
out=$(docker exec nginx-embed-test nginx -t 2>&1)
grep -q 'test is successful' <<<"$out" && echo "ok   nginx.oracle.conf 문법" || { echo "FAIL nginx.oracle.conf: $out"; FAILS=$((FAILS+1)); }

[ "$FAILS" -eq 0 ] && echo "ALL PASS" || { echo "$FAILS 건 실패"; exit 1; }
