#!/bin/sh
# 운영 init 컨테이너(#308): 빌드 산출물을 공유 볼륨에 복사하고, 실행 env 로 runtime-config.js 를 다시 쓴다.
# DSN·release 를 빌드 때 박지 않는다 — 이미지 하나를 운영에 쓰고, 값은 compose 가 env 로 넣는다.
# SENTRY_DSN 이 비면 빈 문자열이 들어가 앱이 SDK 를 켜지 않는다. 값은 화면(로그)에 찍지 않는다.
set -eu
out="${1:-/output}"
src="${RUNTIME_DIST:-/dist}"  # 시험(scripts/test-runtime-config.sh)이 바꾼다
cp -r "$src/." "$out/"
# JS 문자열 안에 넣는다 — 역슬래시·따옴표를 이스케이프하고 줄바꿈은 버린다.
js_string() { printf '%s' "$1" | tr -d '\r\n' | sed 's/\\/\\\\/g; s/"/\\"/g'; }
printf 'window.__FLOW_RUNTIME__ = {"sentryDsn": "%s", "sentryRelease": "%s"}\n' \
  "$(js_string "${SENTRY_DSN:-}")" "$(js_string "${SENTRY_RELEASE:-}")" > "$out/runtime-config.js"
