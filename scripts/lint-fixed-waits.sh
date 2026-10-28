#!/usr/bin/env bash
# 시험의 고정 시간 대기를 막는다(#259). docker 없이 1초 안에 끝난다(pre-commit 훅이 부른다).
#
# 막는 것: waitForTimeout, networkidle, setTimeout/setInterval(시험 한도를 정하는 test.setTimeout 은 제외), sleep(
# 대상: e2e/tests, admin/src·widget/src 의 *.test.* / *.spec.*
# 사건(응답·요소·화면 안정)을 기다린다 — e2e/tests/settle.js 의 settled·trackRequests, web-first 단언, waitForResponse,
# vitest 는 findBy*/waitFor, 시간 로직은 vi.useFakeTimers.
# 정말 사건이 없어 시간으로 기다려야 하는 줄만, 같은 줄 끝에 `// wait-ok: <이유>` 를 적어 푼다.
# (eslint/oxlint 는 e2e·admin·widget 어디에도 없어 새 도구 대신 grep 으로 막는다. BSD/GNU grep 둘 다에서 돈다.)
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

files=$(find e2e/tests admin/src widget/src \( -name node_modules -prune \) -o -type f \( -name '*.test.ts' -o -name '*.test.tsx' -o -name '*.test.js' -o -name '*.spec.ts' -o -name '*.spec.tsx' -o -name '*.spec.js' -o -path 'e2e/tests/*.js' \) -print 2>/dev/null)
[ -n "$files" ] || exit 0
# shellcheck disable=SC2086
hits=$(grep -nE 'waitForTimeout|networkidle|setTimeout\(|setInterval\(|(^|[^A-Za-z_.])sleep\(' $files /dev/null | grep -vE 'test\.setTimeout\(|wait-ok:' || true)
if [ -n "$hits" ]; then
  echo "✗ 시험에 고정 시간 대기가 있습니다(#259). 사건을 기다리세요 — 스크립트 머리의 설명 참고:"
  echo "$hits"
  exit 1
fi
echo "✓ 고정 시간 대기 없음"
