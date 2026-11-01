#!/usr/bin/env bash
# linux/arm64 운영 이미지 3개(api·admin·widget)를 굽고 ghcr에 올린다(#293, ADR-0036).
# Jenkinsfile의 「이미지 빌드」 단계가 macos 또는 home(built-in)에서 부른다. 본보기는 portfolio·AI-Trend-News의
# scripts/ci/build-image.sh다.
#
#   scripts/ci/build-images.sh <macos|home>
#
# 환경변수: GHCR_TOKEN(ghcr 쓰기 토큰, Jenkins 자격 `ghcr-push-token`),
#           GIT_SSH_KEY(비공개 sandcorp-auth를 읽는 키 파일, Jenkins 자격 `github-farrar142`),
#           FLOW_IMAGE_PREFIX(기본 ghcr.io/sandcorporation/sandcorp-flow), FLOW_BUILDER(buildx 빌더 이름)
# 결과: 이미지마다 `<prefix>-<image>:<커밋 sha 40자>`와 `:latest`. 빌드 캐시는 `<prefix>-<image>:buildcache`
#       (레지스트리, mode=max — 두 빌드 노드가 나눠 쓴다).
# bash 3.2(macos 노드)에서도 돈다. 노드의 docker 설정은 건드리지 않고 임시 DOCKER_CONFIG에만 토큰을 적는다.
set -euo pipefail

where="${1:-}"
case "$where" in macos|home) ;; *) echo "쓰는 법: $0 <macos|home>" >&2; exit 2 ;; esac

cd "$(dirname "$0")/../.."
prefix="${FLOW_IMAGE_PREFIX:-ghcr.io/sandcorporation/sandcorp-flow}"
builder="${FLOW_BUILDER:-sandcorp-flow-images}"
sha="$(git -c safe.directory="$PWD" rev-parse HEAD)"
start="$(date +%s)"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/sandcorp-flow-docker.XXXXXX")"
cleanup() {
  rm -rf "$tmp"
  if [ -n "${SSH_AGENT_PID:-}" ]; then ssh-agent -k >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if ! docker buildx version >/dev/null 2>&1; then
  echo "이 노드의 docker CLI에 buildx가 없다" >&2
  exit 1
fi

if [ -n "${GHCR_TOKEN:-}" ]; then
  orig="${DOCKER_CONFIG:-$HOME/.docker}"
  mkdir -p "$orig/buildx"
  for d in cli-plugins buildx contexts; do
    if [ -e "$orig/$d" ]; then ln -s "$orig/$d" "$tmp/$d"; fi
  done
  # auths에 직접 적는다 — `docker login`은 키체인(macos)·pass를 찾다 잠겨서 실패한다.
  python3 - "$orig/config.json" "$tmp/config.json" <<'PY'
import base64, json, os, sys
src, dst = sys.argv[1:3]
try:
    with open(src) as f:
        cfg = json.load(f)
except (OSError, ValueError):
    cfg = {}
for key in ("auths", "credsStore", "credHelpers"):
    cfg.pop(key, None)
tok = os.environ["GHCR_TOKEN"].strip()
cfg["auths"] = {"ghcr.io": {"auth": base64.b64encode(f"sandcorp-flow-ci:{tok}".encode()).decode()}}
fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f)
PY
  export DOCKER_CONFIG="$tmp"
else
  echo "GHCR_TOKEN이 없다 — 이 노드의 docker 로그인을 그대로 쓴다"
fi

# api만 sandcorp-authkit(비공개 sandcorp-auth)을 BuildKit ssh 마운트로 받는다(backend/Dockerfile의
# RUN --mount=type=ssh, #282). 키 경로·내용은 찍지 않는다.
if [ -n "${GIT_SSH_KEY:-}" ]; then
  eval "$(ssh-agent -s)" >/dev/null
  ssh-add "$GIT_SSH_KEY" 2>/dev/null
fi

if ! docker buildx inspect "$builder" >/dev/null 2>&1; then
  echo "빌더 $builder를 만든다(docker-container)"
  docker buildx create --name "$builder" --driver docker-container >/dev/null
fi
docker buildx inspect --bootstrap "$builder" >/dev/null

# 이미지 이름:빌드 문맥. 셋 다 성공해야 이 스크립트가 0으로 끝나고, 그래야 Jenkinsfile이 배포 단계로 간다.
for pair in api:backend admin:admin widget:widget; do
  image="${pair%%:*}"
  context="./${pair#*:}"
  repo="$prefix-$image"
  ssh_arg=""
  if [ "$image" = api ]; then ssh_arg="--ssh default"; fi
  t0="$(date +%s)"
  echo "== $image · 빌드 노드 $where · linux/arm64 · $repo:$sha"
  # shellcheck disable=SC2086  # ssh_arg는 비었거나 두 낱말이다
  docker buildx build --builder "$builder" --platform linux/arm64 --provenance=false --push \
    $ssh_arg \
    --tag "$repo:$sha" \
    --tag "$repo:latest" \
    --label "org.opencontainers.image.source=https://github.com/sandcorporation/sandcorp-flow" \
    --label "org.opencontainers.image.revision=$sha" \
    --cache-from "type=registry,ref=$repo:buildcache" \
    --cache-to "type=registry,ref=$repo:buildcache,mode=max" \
    "$context"
  echo "== $image 완료: $(( $(date +%s) - t0 ))초"
done
echo "이미지 3개 빌드·push 완료 ($where): $(( $(date +%s) - start ))초"
