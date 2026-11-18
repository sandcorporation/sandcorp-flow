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
# FAKE_RUNNING이면 db·redis·nginx가 떠 있다. 이미지 문자열은 옛 태그(old)와 고정 digest(new)로 다르다.
# FAKE_SAME_IMAGE면 두 문자열이 같은 이미지(ID)를 가리키고, 정의 해시도 image 문자열 말고는 같다(첫 고정 배포).
case "$*" in
  "rollout --help") echo "Rollout new Compose service version." ;;
  *" ps -q --status running "*) [ -n "${FAKE_RUNNING:-}" ] && echo "cid-${@: -1}" ;;
  *" ps -q "*) [ -n "${FAKE_RUNNING:-}" ] && echo "cid-${@: -1}" ;;
  *" config --images "*) echo "${@: -1}:new@sha256:abc" ;;
  *" config --hash "*) case "$*" in *" -f "*" -f "*) echo "${@: -1} H-old" ;; *) echo "${@: -1} H-new" ;; esac ;;
  "inspect -f {{.Config.Image}} "*) last=${@: -1}; echo "${last#cid-}:old" ;;
  "inspect -f {{.Image}} "*) echo "sha256:running" ;;
  "inspect -f "*config-hash*) echo "H-old" ;;
  "image inspect -f {{.Id}} "*) if [ -n "${FAKE_PIN_MISSING:-}" ]; then exit 1; fi; if [ -n "${FAKE_SAME_IMAGE:-}" ]; then echo "sha256:running"; else echo "sha256:other"; fi ;;
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
has 'migrate 돈다' 'run --rm --no-deps api python manage.py migrate --noinput'
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

# #360: pull은 우리 앱 이미지(ghcr) 서비스만 — 기반 이미지(db·redis·nginx 등)는 떠다니는 태그를 새로 받지 않는다.
# 운영 compose의 기반(비-ghcr) 이미지는 모두 @sha256: 고정이라 상류 태그가 바뀌어도 다음 배포가 컨테이너를 다시 만들지 않는다.
compose="$root/docker-compose.prod.oracle.yml"
# 서비스 → 명시 image(앵커 <<: *backend로 받는 서비스는 ghcr api 이미지)
base_svcs=$(awk '/^services:/{s=1;next} /^[^ #]/{s=0} s&&/^  [a-z][a-z0-9-]*:$/{svc=substr($1,1,length($1)-1)} s&&/^    image:/{print svc, $2}' "$compose" \
  | awk '$2 !~ /^ghcr\.io\//{print $1}')
[ -n "$base_svcs" ] && pass=$((pass + 1)) || { echo "FAIL 기반 이미지 서비스를 compose에서 못 찾음"; fail=$((fail + 1)); }
while read -r svc img; do
  case "$img" in
    ghcr.io/*) pass=$((pass + 1)) ;;
    *@sha256:*) pass=$((pass + 1)) ;;
    *) echo "FAIL 기반 이미지 digest 고정 — $svc: $img (태그@sha256:… 로 고정)"; fail=$((fail + 1)) ;;
  esac
done < <(awk '/^services:/{s=1;next} /^[^ #]/{s=0} s&&/^  [a-z][a-z0-9-]*:$/{svc=substr($1,1,length($1)-1)} s&&/^    image:/{print svc, $2}' "$compose")
run pullonly 0 SANDCORP_IMAGE_SHA=abc999
pull_line=$(grep -F ' pull' "$LAST/log" | head -n 1)
for svc in api worker worker-chat events widget-init admin-init; do
  case " $pull_line " in *" $svc "*) pass=$((pass + 1)) ;; *) echo "FAIL pull 대상에 $svc 없음: $pull_line"; fail=$((fail + 1)) ;; esac
done
for svc in $base_svcs; do
  case " $pull_line " in *" $svc "*) echo "FAIL pull 대상에 기반 이미지 서비스 $svc: $pull_line"; fail=$((fail + 1)) ;; *) pass=$((pass + 1)) ;; esac
done
# migrate run이 db·redis를 의존으로 다시 올리면(정의 해시가 다르면 재생성) 위 판단이 무너진다 — --no-deps.
has 'migrate는 의존을 다시 올리지 않음' 'run --rm --no-deps api python manage.py migrate --noinput'

# 처음 떠 있지 않으면 up(그리고 healthy까지 기다려 migrate 전제를 지킨다).
has '안 떠 있으면 db up' 'up -d --no-deps --wait db'
has '안 떠 있으면 redis up' 'up -d --no-deps --wait redis'

# 첫 고정 배포: 떠 있는 db·redis·nginx가 image 문자열만 다르고 같은 이미지 → up 하지 않는다(재생성 안 함).
run pinsame 0 FAKE_RUNNING=1 FAKE_SAME_IMAGE=1 FAKE_NGINX_SAME=1 SANDCORP_IMAGE_SHA=abc777
lacks '같은 이미지면 db·redis up 안 함' 'up -d --no-deps --wait'
lacks '같은 이미지면 nginx up 안 함' 'up -d --no-deps nginx'
grep -q 'db 유지' "$LAST/out" && grep -q 'redis 유지' "$LAST/out" && grep -q 'nginx 유지' "$LAST/out" && pass=$((pass + 1)) \
  || { echo "FAIL 유지 문구(배포 로그에 재생성 안 함이 보여야)"; tail -n 30 "$LAST/out"; fail=$((fail + 1)); }

# digest를 올린 PR(다른 이미지)은 up — compose가 재생성한다(의도된 올림). 로그에 재생성 사실이 남는다.
run pinbump 0 FAKE_RUNNING=1 FAKE_NGINX_SAME=1 SANDCORP_IMAGE_SHA=abc888
has '다른 이미지면 db up' 'up -d --no-deps --wait db'
has '다른 이미지면 redis up' 'up -d --no-deps --wait redis'
has '다른 이미지면 nginx up' 'up -d --no-deps nginx'
grep -q 'nginx: 건너뛰기 불가 — 이미지 ID가 다르다' "$LAST/out" && pass=$((pass + 1)) \
  || { echo "FAIL 건너뛰기 불가 까닭 줄(이미지 ID)"; tail -n 20 "$LAST/out"; fail=$((fail + 1)); }

# #362: 고정한 digest 이미지가 호스트에 없으면(떠 있는 것과 다른 digest) nginx도 db·redis와 같은 경로로 up하고 까닭을 남긴다.
run pinmissing 0 FAKE_RUNNING=1 FAKE_PIN_MISSING=1 FAKE_NGINX_SAME=1 SANDCORP_IMAGE_SHA=abc889
has '고정 이미지 없으면 nginx up' 'up -d --no-deps nginx'
grep -q 'nginx: 건너뛰기 불가 — compose가 고정한 이미지' "$LAST/out" && grep -q 'db: 건너뛰기 불가' "$LAST/out" && pass=$((pass + 1)) \
  || { echo "FAIL 건너뛰기 불가 까닭 줄(고정 이미지 없음)"; tail -n 20 "$LAST/out"; fail=$((fail + 1)); }

run nosha 2
lacks 'sha 없으면 아무것도 안 함' 'pull'

run smoke 1 FAKE_SMOKE_FAIL=1 SANDCORP_IMAGE_SHA=ddd444

echo "deploy.sh 시험: 통과 $pass · 실패 $fail"
[ "$fail" = 0 ]
