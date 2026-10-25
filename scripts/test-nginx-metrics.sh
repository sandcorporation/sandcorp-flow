#!/usr/bin/env bash
# 운영 nginx(nginx.oracle.conf)의 방문 지표 스니펫 주입·webmanifest 타입 시험 (#154).
# 실제 nginx:1.27-alpine 에 운영 conf·템플릿을 그대로 올려 curl 로 단언한다(정적 파일은 최소 픽스처).
#   bash scripts/test-nginx-metrics.sh
set -uo pipefail
cd "$(dirname "$0")/.."
FIX=$(mktemp -d)
IMG=nginx:1.27-alpine
PORT=${NGINX_TEST_PORT:-18297}
FAILS=0
cleanup() { docker rm -f nginx-metrics-test >/dev/null 2>&1; docker run --rm -v "$FIX:/w" alpine rm -rf /w/widget /w/admin >/dev/null 2>&1; rmdir "$FIX" 2>/dev/null; }
trap cleanup EXIT

mkdir -p "$FIX/widget/flow" "$FIX/admin"
page='<!doctype html><html><head><title>t</title></head><body>x</body></html>'
for f in landing privacy terms index; do echo "$page" > "$FIX/widget/$f.html"; done
echo "$page" > "$FIX/admin/index.html"
echo '{"name":"Flow"}' > "$FIX/widget/flow/site.webmanifest"
chmod -R a+rX "$FIX"

start() { # $1=URL $2=KEY
  docker rm -f nginx-metrics-test >/dev/null 2>&1
  # compose 와 같게: 변수는 늘 정의(비어 있을 수 있음), 키 기본값은 flow.
  docker run -d --name nginx-metrics-test -p "127.0.0.1:$PORT:80" \
    -e NGINX_ENVSUBST_FILTER='^METRICS_' -e "METRICS_SNIPPET_URL=${1:-}" -e "METRICS_SITE_KEY=${2:-flow}" \
    -v "$PWD/backend/nginx/nginx.oracle.conf:/etc/nginx/nginx.conf:ro" \
    -v "$PWD/backend/nginx/flow-metrics.conf.template:/etc/nginx/templates/flow-metrics.conf.template:ro" \
    -v "$FIX/widget:/app/widget:ro" -v "$FIX/admin:/app/admin:ro" "$IMG" >/dev/null
  for _ in $(seq 1 40); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/terms" && return 0; sleep 0.25; done
  docker logs nginx-metrics-test 2>&1 | tail -5; FAILS=$((FAILS+1)); echo "FAIL nginx 기동"
}
check() { # $1=이름 $2=경로 $3=has|none
  local body; body=$(curl -s "http://127.0.0.1:$PORT$2")
  local has=none; grep -q '<script defer src=' <<<"$body" && has=has
  if [ "$has" = "$3" ]; then echo "ok   $1 $2 ($3)"; else echo "FAIL $1 $2: 기대 $3, 실제 $has"; FAILS=$((FAILS+1)); fi
}
URL='https://metrics.sandcorp.co.kr/m.js'

echo "== env 있음 =="
start "$URL" ""
for p in / /terms /privacy /admin-ui/ /admin-ui/try; do check on "$p" has; done
for p in /embed/ /chatbot/sandcorp/ /embed/index.html; do check on "$p" none; done
curl -s "http://127.0.0.1:$PORT/terms" | grep -q "<script defer src=\"$URL\" data-site=\"flow\"></script></head>" \
  && echo "ok   태그 모양(data-site 기본 flow, head 끝)" || { echo "FAIL 태그 모양"; FAILS=$((FAILS+1)); }
start "$URL" "other"
curl -s "http://127.0.0.1:$PORT/" | grep -q 'data-site="other"' && echo "ok   METRICS_SITE_KEY 반영" || { echo "FAIL METRICS_SITE_KEY"; FAILS=$((FAILS+1)); }

echo "== env 없음(비움) =="
start "" ""
for p in / /terms /privacy /admin-ui/ /admin-ui/try /embed/ /chatbot/sandcorp/; do check off "$p" none; done
curl -s "http://127.0.0.1:$PORT/terms" | grep -q 'src=""' && { echo "FAIL 빈 src"; FAILS=$((FAILS+1)); } || echo "ok   빈 src 없음"

echo "== 짧은 주소(#165): 301 + 쿼리 보존, 이웃 location 과 겹치지 않음 =="
start "$URL" ""
loc() { # $1=경로 → "상태 Location"
  curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "http://127.0.0.1:$PORT$1" | sed "s#http://127.0.0.1:$PORT##"
}
expect_loc() { # $1=경로 $2=기대 "상태 Location"
  local got; got=$(loc "$1")
  if [ "$got" = "$2" ]; then echo "ok   $1 -> $got"; else echo "FAIL $1: 기대 '$2', 실제 '$got'"; FAILS=$((FAILS+1)); fi
}
expect_loc /try "301 /admin-ui/try"
expect_loc "/try?template=home" "301 /admin-ui/try?template=home"
expect_loc "/try?template=shop&x=1" "301 /admin-ui/try?template=shop&x=1"
expect_loc "/try/?template=academy" "301 /admin-ui/try?template=academy"
expect_loc /flows/sandcorp "301 /admin-ui/flows/sandcorp"
expect_loc "/flows/sandcorp?a=b" "301 /admin-ui/flows/sandcorp?a=b"
expect_loc "/flows/%ED%95%9C%EA%B8%80" "301 /admin-ui/flows/%ED%95%9C%EA%B8%80"
# 겹침 없음: /tryX·/flowsX 는 짧은 주소가 아니다(404), 기존 location 은 그대로.
for p in /tryout /try-it /flowsX /flows; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT$p")
  [ "$code" = 404 ] && echo "ok   $p 404(짧은 주소 아님)" || { echo "FAIL $p: 404 기대, 실제 $code"; FAILS=$((FAILS+1)); }
done
for p in / /terms /privacy /admin-ui/ /admin-ui/try /embed/ /chatbot/sandcorp/; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT$p")
  [ "$code" = 200 ] && echo "ok   $p 200 유지" || { echo "FAIL $p: 200 기대, 실제 $code"; FAILS=$((FAILS+1)); }
done
# 리다이렉트 응답엔 스니펫이 없고(HTML 본문이 아님), 도착한 admin 페이지에는 한 번만 붙는다.
n=$(curl -sL "http://127.0.0.1:$PORT/try?template=home" | grep -o '<script defer src=' | wc -l)
[ "$n" = 1 ] && echo "ok   /try 를 따라가 도착한 페이지에 스니펫 1개" || { echo "FAIL 짧은 주소 스니펫 수: $n"; FAILS=$((FAILS+1)); }
n=$(curl -sL "http://127.0.0.1:$PORT/flows/sandcorp" | grep -o '<script defer src=' | wc -l)
[ "$n" = 1 ] && echo "ok   /flows/sandcorp 를 따라가 스니펫 1개" || { echo "FAIL 흐름 보기 스니펫 수: $n"; FAILS=$((FAILS+1)); }

echo "== webmanifest =="
ct=$(curl -sI "http://127.0.0.1:$PORT/embed/flow/site.webmanifest" | tr -d '\r' | awk -F': ' 'tolower($1)=="content-type"{print $2}')
[ "$ct" = "application/manifest+json" ] && echo "ok   content-type $ct" || { echo "FAIL webmanifest content-type: '$ct'"; FAILS=$((FAILS+1)); }

[ "$FAILS" -eq 0 ] && echo "ALL PASS" || { echo "$FAILS 건 실패"; exit 1; }
