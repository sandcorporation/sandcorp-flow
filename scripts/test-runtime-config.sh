#!/usr/bin/env bash
# admin·widget 의 runtime-config.sh(운영 init 컨테이너, #308) 시험 — docker 없이 1초 안에 끝난다.
# scripts/ci/check.sh ①이 부른다.
#
#   bash scripts/test-runtime-config.sh
#
# 보는 것: 산출물 복사, env 가 있으면 그 값이, 없으면 빈 값이 runtime-config.js 에 들어감,
#          따옴표·역슬래시·줄바꿈이 JS 문자열을 깨지 않음, 두 앱의 스크립트가 같음.
set -uo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/flow-runtime-config-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT
fail=0
check() { # 이름, 기대, 실제
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1"; echo "  기대: $2"; echo "  실제: $3"; fail=1; fi
}

if ! cmp -s "$root/admin/runtime-config.sh" "$root/widget/runtime-config.sh"; then
  echo "FAIL admin/runtime-config.sh 와 widget/runtime-config.sh 가 다르다(같은 내용으로 복사해 둔다)"; fail=1
fi

mkdir -p "$work/dist/assets" "$work/out"
echo '<html></html>' > "$work/dist/index.html"
echo 'js' > "$work/dist/assets/a.js"

run() { env -i PATH="$PATH" RUNTIME_DIST="$work/dist" "$@" sh "$root/admin/runtime-config.sh" "$work/out"; }

run SENTRY_DSN='https://pub@glitchtip.example/3' SENTRY_RELEASE='0123456789abcdef0123456789abcdef01234567'
check "산출물 복사" "js" "$(cat "$work/out/assets/a.js")"
check "값이 들어감" \
  'window.__FLOW_RUNTIME__ = {"sentryDsn": "https://pub@glitchtip.example/3", "sentryRelease": "0123456789abcdef0123456789abcdef01234567"}' \
  "$(cat "$work/out/runtime-config.js")"

run
check "env 없으면 빈 값(SDK 꺼짐)" \
  'window.__FLOW_RUNTIME__ = {"sentryDsn": "", "sentryRelease": ""}' \
  "$(cat "$work/out/runtime-config.js")"

run SENTRY_DSN="a\"b\\c
d"
check "따옴표·역슬래시 이스케이프, 줄바꿈 버림" \
  'window.__FLOW_RUNTIME__ = {"sentryDsn": "a\"b\\cd", "sentryRelease": ""}' \
  "$(cat "$work/out/runtime-config.js")"

exit "$fail"
