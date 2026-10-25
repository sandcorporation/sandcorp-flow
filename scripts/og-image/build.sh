#!/usr/bin/env bash
# 링크 공유 카드(og:image, 1200x630)를 og-*.svg 에서 다시 만든다 (#186). 결과 PNG 는 커밋한다.
#   widget/public/flow/og.png ← og-landing.svg (랜딩·약관·처리방침)
#   admin/public/flow/og.png  ← og-admin.svg   (/admin-ui/, /try)
# 렌더는 docker 안의 헤드리스 Chromium(ghcr.io/browserless/chromium, 한글 글꼴 포함)이 한다.
# Pretendard(Bold·SemiBold .otf)는 저장소에 두지 않는다: PRETENDARD_DIR 로 폴더를 준다
# (기본: sandcorp-design 의 node_modules — pnpm install 한 곳).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
fonts="${PRETENDARD_DIR:-$HOME/workspace/sandcorp-design/node_modules/.pnpm/pretendard@1.3.9/node_modules/pretendard/dist/public/static}"
[ -f "$fonts/Pretendard-Bold.otf" ] || { echo "Pretendard-Bold.otf 없음: PRETENDARD_DIR=$fonts" >&2; exit 1; }
IMG=ghcr.io/browserless/chromium:v2.30.0
tmp="$(mktemp -d)"
trap 'rm -f "$tmp"/in.html "$tmp"/out.png; rmdir "$tmp" 2>/dev/null || true' EXIT
render() { # $1=svg $2=출력 PNG
  {
    echo "<!doctype html><meta charset=utf-8><style>"
    echo "@font-face{font-family:Pretendard;font-weight:700;src:url('file:///fonts/Pretendard-Bold.otf')}"
    echo "@font-face{font-family:Pretendard;font-weight:600;src:url('file:///fonts/Pretendard-SemiBold.otf')}"
    echo "html,body{margin:0;background:#fff}svg{display:block}</style>"
    cat "$here/$1"
  } > "$tmp/in.html"
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp --entrypoint /usr/local/bin/playwright-browsers/chromium_headless_shell-1169/chrome-linux/headless_shell \
    -v "$fonts:/fonts:ro" -v "$tmp:/w" "$IMG" \
    --headless --no-sandbox --disable-gpu --user-data-dir=/tmp/p --hide-scrollbars --force-device-scale-factor=1 \
    --window-size=1200,630 --virtual-time-budget=3000 --screenshot=/w/out.png file:///w/in.html >/dev/null 2>&1
  mkdir -p "$(dirname "$2")"; cp "$tmp/out.png" "$2"; echo "$2"
}
render og-landing.svg "$root/widget/public/flow/og.png"
render og-admin.svg "$root/admin/public/flow/og.png"
