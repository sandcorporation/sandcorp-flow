#!/usr/bin/env bash
# PR 검사: GitHub Actions(.github/workflows/pr-check.yml)가 PR 머지 결과로 단계마다(CHECK_ONLY) 부른다. 로컬에서도 그대로 돈다.
#
#   bash scripts/ci/check.sh            # 전부
#   CHECK_ONLY="backend vitest" bash scripts/ci/check.sh   # 일부만(lint backend vitest openapi nginx e2e)
#
# 단계(⑤는 ②~④와 나란히): ① 고정 대기 lint·deploy.sh 시험(가짜 docker) ② 백엔드 pytest(db·redis·fake-llm) ③ admin·widget vitest + widget typecheck
#       ④ OpenAPI·orval 생성물 드리프트 ⑤ nginx 계약 시험 ⑥ e2e(Playwright)
# - compose 프로젝트 이름을 이 실행만의 것(flow-check-…)으로 덮어 dev·로컬 시험 스택, 다른 검사와 겹치지 않는다.
#   끝나면 그 프로젝트의 컨테이너·볼륨·구운 이미지를 지운다(빌드 캐시는 남아 다음 실행이 빠르다).
# - 소스는 bind mount 하지 않고 이미지에 굽거나(tar로) 컨테이너에 넣는다.
# - sandcorp-authkit 은 공개 sandcorp-auth 에서 git+https 로 받는다(ssh 전달 불필요, #320).
set -euo pipefail
cd "$(dirname "$0")/../.."

run_id="flow-check-$(date +%s)-$$"
only=" ${CHECK_ONLY:-lint backend vitest openapi nginx e2e} "
want() { case "$only" in *" $1 "*) return 0 ;; esac; return 1; }
# e2e backend 이미지(api-e2e 가 굽고 worker·events 가 같이 쓴다, #332)는 이름이 붙어 down --rmi local 이 안 지운다 — 실행마다 이름을 주고 cleanup 에서 지운다
export E2E_BACKEND_IMAGE="$run_id-backend-e2e:local"
T=(docker compose -p "$run_id" -f docker-compose.test.yml)
started="$(date +%s)"
lap() { echo "== $1 끝: $(( $(date +%s) - t0 ))초"; }

cleanup() {
  if [ -n "${nginx_pid:-}" ]; then kill "$nginx_pid" >/dev/null 2>&1 || true; fi
  rm -f "${nginx_log:-}"
  "${T[@]}" down -v --remove-orphans --rmi local >/dev/null 2>&1 || true
  docker image rm "$E2E_BACKEND_IMAGE" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# node 컨테이너에서 admin·widget 시험. 소스는 tar 로 넣는다(node_modules 제외).
# admin 시험은 /e2e/narrow-routes.json(e2e 라우트 목록)을 읽는다. $1 = admin|widget, $2 = 그 안에서 돌릴 명령
node_run() {
  # macos(bsdtar)는 확장 속성을 ._<파일> 로 끼워 넣어 vitest 가 그것까지 시험 파일로 읽는다 — COPYFILE_DISABLE·._* 제외.
  COPYFILE_DISABLE=1 tar -cf - --exclude=node_modules --exclude=dist --exclude='._*' "$1" e2e/tests/narrow-routes.json \
    | docker run -i --rm node:20-alpine sh -euc "mkdir -p /src /e2e && cd /src && tar -xf - \
        && cp e2e/tests/narrow-routes.json /e2e/ && cd $1 && npm ci --no-audit --no-fund --loglevel=error && $2"
}

if want lint; then
  t0="$(date +%s)"; echo "== ① 고정 대기 lint · 배포 스크립트 시험"
  bash scripts/lint-fixed-waits.sh
  bash scripts/test-deploy.sh
  bash scripts/test-runtime-config.sh
  bash scripts/ci/promote_test.sh
  bash scripts/ci/test-actions-only-pipeline.sh
  lap ①
fi

# ⑤ nginx 계약 시험은 컨테이너 기동·응답을 기다리는 시간이 대부분이라(CPU 를 거의 안 쓴다) ②~④와 나란히 돌리고 ⑥ 전에 거둔다.
nginx_pid=""
nginx_log="$(mktemp "${TMPDIR:-/tmp}/flow-nginx.XXXXXX")"
if want nginx; then
  echo "== ⑤ nginx 계약 시험 — 배경에서 시작(로그는 ⑥ 전에)"
  (
    t0="$(date +%s)"
    bash scripts/test-nginx-seo.sh && bash scripts/test-nginx-metrics.sh && bash scripts/test-nginx-embed-policy.sh
    rc=$?
    echo "== ⑤ 끝: $(( $(date +%s) - t0 ))초 (종료 $rc)"
    exit "$rc"
  ) > "$nginx_log" 2>&1 &
  nginx_pid=$!
fi

if want backend || want openapi; then
  t0="$(date +%s)"; echo "== 시험 이미지(backend Dockerfile.test·fake-llm)"
  "${T[@]}" build test fake-llm
  lap 이미지
fi

if want backend; then
  t0="$(date +%s)"; echo "== ② 백엔드 pytest"
  "${T[@]}" run --rm -e TEST_CPU_LIMIT test python -m pytest -p no:cacheprovider tests/ -q --tb=short
  lap ②
fi

if want vitest; then
  t0="$(date +%s)"; echo "== ③ admin vitest"
  node_run admin 'npm run test:run -- --reporter=dot'
  echo "== ③ widget vitest · typecheck"
  node_run widget 'npm run typecheck && npm run test:run -- --reporter=dot'
  lap ③
fi

if want openapi; then
  t0="$(date +%s)"; echo "== ④ OpenAPI·orval 생성물 드리프트(bash scripts/gen-admin-api.sh 로 고친다)"
  spec="$(mktemp "${TMPDIR:-/tmp}/flow-openapi.XXXXXX")"
  "${T[@]}" run --rm --no-deps test sh -c 'python manage.py export_openapi --output /tmp/openapi.json >/dev/null && cat /tmp/openapi.json' > "$spec"
  if ! cmp -s "$spec" admin/openapi.json; then
    rm -f "$spec"
    echo "admin/openapi.json 이 백엔드와 다르다 — bash scripts/gen-admin-api.sh 로 다시 만들어 함께 커밋한다" >&2
    exit 1
  fi
  rm -f "$spec"
  node_run admin 'cp -R src/generated /tmp/generated.orig && npx orval --config ./orval.config.ts >/dev/null \
    && if ! diff -r /tmp/generated.orig src/generated >/dev/null; then diff -r /tmp/generated.orig src/generated | head -n 40; \
       echo "admin/src/generated 가 openapi.json 과 다르다 — bash scripts/gen-admin-api.sh" >&2; exit 1; fi'
  lap ④
fi

if [ -n "$nginx_pid" ]; then
  echo "== ⑤ nginx 계약 시험 거두기"
  nginx_rc=0
  wait "$nginx_pid" || nginx_rc=$?
  nginx_pid=""
  cat "$nginx_log"
  if [ "$nginx_rc" != 0 ]; then echo "== ⑤ nginx 계약 시험 실패" >&2; exit 1; fi
fi

if want e2e; then
  t0="$(date +%s)"; echo "== ⑥ e2e 이미지"
  "${T[@]}" build
  lap '⑥ 이미지'
  t0="$(date +%s)"; echo "== ⑥ e2e(Playwright)"
  if ! "${T[@]}" run --rm e2e; then
    echo "== e2e 실패 — api-e2e·worker 로그 끝" >&2
    "${T[@]}" logs --tail 40 api-e2e chat-worker-e2e worker-e2e >&2 || true
    exit 1
  fi
  lap ⑥
fi
echo "== 검사 통과: $(( $(date +%s) - started ))초"
