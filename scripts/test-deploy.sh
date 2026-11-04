#!/usr/bin/env bash
# scripts/deploy.sh 시험 — 가짜 docker로 돌려 이미지 태그·migrate·자동 롤백 경로를 본다(jenkins-shared #9).
# docker·운영 없이 1초 안에 끝난다. scripts/ci/check.sh ①이 부른다.
#
#   bash scripts/test-deploy.sh
#
# 보는 것: 인자/SANDCORP_IMAGE_SHA → IMAGE_TAG, SANDCORP_ROLLBACK=1이면 migrate 건너뜀(그 밖의 순서는 같음),
#          sha가 없으면 2로 끝남, 스모크 실패면 0이 아니게 끝남(그래야 라이브러리가 자동 롤백한다).
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/flow-deploy-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"
# 부른 명령을 IMAGE_TAG와 함께 $FAKE_LOG에 적는다. rollout 플러그인 확인 문구를 낸다. FAKE_SMOKE_FAIL이면 스모크(exec api) 실패.
cat > "$work/bin/docker" <<'SH'
#!/usr/bin/env bash
echo "[${IMAGE_TAG:-}] docker $*" >> "$FAKE_LOG"
case "$*" in
  "rollout --help") echo "Rollout new Compose service version." ;;
  *" exec -T nginx "*) if [ -n "${FAKE_NGINX_SAME:-}" ]; then : ; else exit 1; fi ;;
  *" exec -T api "*) [ -n "${FAKE_SMOKE_FAIL:-}" ] && exit 1 ;;
esac
exit 0
SH
chmod +x "$work/bin/docker"
pass=0
fail=0

# run <이름> <기대 종료코드> [VAR=값 ...] -- [인자...]
run() {
  local name=$1 want=$2
  shift 2
  local envs=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do envs+=("$1"); shift; done
  [ "${1:-}" = -- ] && shift
  local ws="$work/$name"
  mkdir -p "$ws/scripts" "$ws/backend/nginx"
  cp "$root/scripts/deploy.sh" "$ws/scripts/"
  : > "$ws/backend/nginx/nginx.oracle.conf"
  : > "$ws/.env"
  : > "$ws/log"
  (cd "$ws" && env -u SANDCORP_IMAGE_SHA -u SANDCORP_ROLLBACK PATH="$work/bin:$PATH" FAKE_LOG="$ws/log" \
     ${envs[@]+"${envs[@]}"} bash scripts/deploy.sh "$@" > "$ws/out" 2>&1)
  local got=$?
  LAST="$ws"
  if [ "$got" = "$want" ]; then pass=$((pass + 1)); else echo "FAIL $name: 종료 $got (기대 $want)"; tail -n 15 "$ws/out"; fail=$((fail + 1)); fi
}
has() { if grep -qF -- "$2" "$LAST/log"; then pass=$((pass + 1)); else echo "FAIL $1 — '$2' 없음"; fail=$((fail + 1)); fi; }
lacks() { if grep -qF -- "$2" "$LAST/log"; then echo "FAIL $1 — '$2' 있음"; fail=$((fail + 1)); else pass=$((pass + 1)); fi; }

run arg 0 -- aaa111
has '인자 sha로 pull' '[aaa111] docker compose -f docker-compose.prod.oracle.yml pull'
has 'migrate 돈다' 'run --rm api python manage.py migrate --noinput'
has 'api 롤링' '[aaa111] docker rollout -t 180 -f docker-compose.prod.oracle.yml api'

run env 0 SANDCORP_IMAGE_SHA=bbb222 SANDCORP_ROLLBACK=0
has 'SANDCORP_IMAGE_SHA로 pull' '[bbb222] docker compose -f docker-compose.prod.oracle.yml pull'
has 'ROLLBACK=0이면 migrate' 'manage.py migrate'

run rollback 0 SANDCORP_IMAGE_SHA=ccc333 SANDCORP_ROLLBACK=1
lacks '자동 롤백은 migrate 건너뜀' 'manage.py migrate'
has '롤백도 옛 이미지 pull' '[ccc333] docker compose -f docker-compose.prod.oracle.yml pull'
has '롤백도 정적 갱신' 'up -d --no-deps widget-init admin-init'
has '롤백도 worker-chat 롤링' '[ccc333] docker rollout -t 180 -f docker-compose.prod.oracle.yml worker-chat'
has '롤백도 스모크' 'exec -T api python'

# nginx 가드(#302): conf가 같아도 compose가 정의(포트·볼륨 등) 변경을 보고 재생성하도록 force 없는 up을 항상 부른다.
# IMAGE_TAG는 nginx 정의에 안 들어가므로 태그만 바뀐 배포는 compose가 no-op으로 둔다(무중단) — force는 conf 변경 때만.
run ngsame 0 FAKE_NGINX_SAME=1 SANDCORP_IMAGE_SHA=eee555
has 'conf 같아도 compose가 정의를 판단' 'up -d --no-deps nginx'
lacks 'conf 같으면 force-recreate 안 함' 'force-recreate'
grep -q '변경 없음' "$LAST/out" && pass=$((pass + 1)) || { echo "FAIL 변경 없음 문구"; fail=$((fail + 1)); }

run ngdiff 0 SANDCORP_IMAGE_SHA=fff666
has 'conf 다르면 force-recreate' 'up -d --no-deps --force-recreate nginx'

run nosha 2
lacks 'sha 없으면 아무것도 안 함' 'pull'

run smoke 1 FAKE_SMOKE_FAIL=1 SANDCORP_IMAGE_SHA=ddd444

echo "deploy.sh 시험: 통과 $pass · 실패 $fail"
[ "$fail" = 0 ]
