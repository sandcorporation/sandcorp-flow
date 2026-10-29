#!/usr/bin/env bash
# 운영 nginx(nginx.oracle.conf)의 robots.txt·sitemap.xml 시험 (#286).
# 실제 nginx:1.27-alpine 에 운영 conf 를 그대로 올려 상태·Content-Type·내용·지표 스니펫 미주입·기존 경로 회귀를 단언한다.
#   bash scripts/test-nginx-seo.sh
set -uo pipefail
cd "$(dirname "$0")/.."
FIX=$(mktemp -d)
IMG=nginx:1.27-alpine
PORT=${NGINX_TEST_PORT:-18299}
FAILS=0
cleanup() { docker rm -f nginx-seo-test >/dev/null 2>&1; docker run --rm -v "$FIX:/w" alpine rm -rf /w/widget /w/admin >/dev/null 2>&1; rmdir "$FIX" 2>/dev/null; }
trap cleanup EXIT

mkdir -p "$FIX/widget" "$FIX/admin"
page='<!doctype html><html><head><title>t</title></head><body>x</body></html>'
for f in landing privacy terms index; do echo "$page" > "$FIX/widget/$f.html"; done
echo "$page" > "$FIX/admin/index.html"
chmod -R a+rX "$FIX"

docker run -d --name nginx-seo-test -p "127.0.0.1:$PORT:80" \
  -e NGINX_ENVSUBST_FILTER='^(METRICS_|TRY_EMBED_)' -e TRY_EMBED_ORIGINS= -e METRICS_SNIPPET_URL=https://metrics.sandcorp.co.kr/m.js -e METRICS_SITE_KEY=flow \
  -v "$PWD/backend/nginx/nginx.oracle.conf:/etc/nginx/nginx.conf:ro" \
  -v "$PWD/backend/nginx/flow-metrics.conf.template:/etc/nginx/templates/flow-metrics.conf.template:ro" \
  -v "$FIX/widget:/app/widget:ro" -v "$FIX/admin:/app/admin:ro" "$IMG" >/dev/null
for _ in $(seq 1 40); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/terms" && break; sleep 0.25; done

U="http://127.0.0.1:$PORT"
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; FAILS=$((FAILS+1)); }

echo "== robots.txt =="
h=$(curl -si "$U/robots.txt"); b=$(curl -s "$U/robots.txt")
head -1 <<<"$h" | grep -q ' 200' && ok "200" || bad "robots 상태"
grep -qi '^content-type: text/plain' <<<"$h" && ok "text/plain" || bad "robots Content-Type"
for l in 'User-agent: *' 'Allow: /' 'Allow: /admin-ui/try' 'Disallow: /admin-ui/' 'Disallow: /api/' 'Disallow: /mcp' 'Disallow: /chatbot/' 'Disallow: /embed' 'Disallow: /.well-known/' 'Disallow: /_internal/' 'Disallow: /flows/' 'Sitemap: https://flow.sandcorp.co.kr/sitemap.xml'; do
  grep -qxF "$l" <<<"$b" && ok "줄 '$l'" || bad "robots 에 줄 없음: $l"
done
grep -qi 'content-security-policy' <<<"$h" && bad "robots 에 CSP" || ok "CSP 없음"
grep -q '<script' <<<"$b" && bad "robots 에 지표 스니펫" || ok "지표 스니펫 없음"

echo "== sitemap.xml =="
h=$(curl -si "$U/sitemap.xml"); b=$(curl -s "$U/sitemap.xml")
head -1 <<<"$h" | grep -q ' 200' && ok "200" || bad "sitemap 상태"
grep -qi '^content-type: application/xml' <<<"$h" && ok "application/xml" || bad "sitemap Content-Type: $(grep -i '^content-type' <<<"$h")"
python3 - "$b" <<'PY' && ok "xml 형식·공개 URL 만" || bad "sitemap 내용"
import sys, xml.etree.ElementTree as ET
ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
locs = [e.text for e in ET.fromstring(sys.argv[1]).findall("s:url/s:loc", ns)]
base = "https://flow.sandcorp.co.kr"
assert locs == [base + p for p in ("/", "/admin-ui/try", "/privacy", "/terms")], locs
PY
grep -q '<script' <<<"$b" && bad "sitemap 에 지표 스니펫" || ok "지표 스니펫 없음"

echo "== 회귀: 기존 경로 =="
code() { curl -s -o /dev/null -w '%{http_code}' "$U$1"; }
for p in / /privacy /terms /admin-ui/ /admin-ui/try; do [ "$(code $p)" = 200 ] && ok "$p 200" || bad "$p"; done
curl -s "$U/" | grep -q 'metrics.sandcorp.co.kr' && ok "/ 지표 스니펫 그대로" || bad "/ 지표 스니펫"
[ "$(code /try)" = 301 ] && ok "/try 301" || bad "/try"
[ "$(code /robots.txt.bak)" != 200 ] && ok "/robots.txt.bak 걸리지 않음" || bad "robots 정확 일치"

echo; [ "$FAILS" = 0 ] && echo "모두 통과" || { echo "실패 $FAILS건"; exit 1; }
