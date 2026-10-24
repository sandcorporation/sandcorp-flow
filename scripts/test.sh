#!/usr/bin/env bash
# backend 단위/통합 테스트를 풀스택(db·redis·ollama)으로 돌린 뒤, 테스트 전용 인프라를 자동 중지한다.
#
# - 기본 구성은 GPU 없이 돈다(ollama CPU). `docker info`에 nvidia 런타임이 있으면
#   docker-compose.test.gpu.yml을 겹쳐 ollama를 GPU로 돈다. TEST_GPU=0/1로 강제할 수 있다.
# - 코드 변경 즉시 반영을 위해 호스트 backend/를 컨테이너에 마운트한다(rebuild 불필요).
#   리눅스에선 컨테이너를 호스트 uid:gid로 돌려, 마운트 안에 root 소유 파일(media/ 등)을 남기지 않는다.
# - 종료 시(성공/실패/중단 무관) db/redis/ollama 테스트 컨테이너를 stop하여
#   메모리(GPU면 VRAM)를 돌려준다. stop이라 다음 실행은 start로 빠르게 재개된다(워밍업 회피).
# - e2e 서비스(api-e2e 등)는 별도 워크플로우이므로 건드리지 않는다.
#
# 사용법:
#   bash scripts/test.sh
#   bash scripts/test.sh tests/test_rag.py
#   bash scripts/test.sh tests/test_rag.py::test_pdf_normal_text_skips_ocr
set -uo pipefail
cd "$(dirname "$0")/.."

COMPOSE_FILES=(-f docker-compose.test.yml)
INFRA=(test db-test redis-test ollama-test ollama-test-init)
TARGET=("${@:-tests/}")

GPU="${TEST_GPU:-}"
if [ -z "$GPU" ]; then
    if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then GPU=1; else GPU=0; fi
fi
if [ "$GPU" = "1" ]; then
    COMPOSE_FILES+=(-f docker-compose.test.gpu.yml)
    echo "[test.sh] GPU 구성(nvidia 런타임)으로 실행" >&2
else
    echo "[test.sh] CPU 구성으로 실행(TEST_GPU=1로 GPU 강제)" >&2
fi

# Git Bash(MSYS)에서 '/app' 인자와 -v 경로가 호스트 경로로 변환되는 것을 막는다.
# 또한 Git Bash는 Windows 경로(pwd -W)를 줘야 Docker 볼륨 마운트가 동작한다.
RUN_USER=()
if HOST_ROOT="$(pwd -W 2>/dev/null)"; then
    :  # Windows(Docker Desktop): 소유권 문제가 없으므로 기본 사용자로 돈다.
else
    HOST_ROOT="$(pwd)"
    RUN_USER=(--user "$(id -u):$(id -g)" -e HOME=/tmp)
fi
HOST_BACKEND="${HOST_ROOT}/backend"

cleanup() {
    MSYS_NO_PATHCONV=1 docker compose "${COMPOSE_FILES[@]}" stop "${INFRA[@]}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

MSYS_NO_PATHCONV=1 docker compose "${COMPOSE_FILES[@]}" run --rm \
    "${RUN_USER[@]}" -v "${HOST_BACKEND}:/app" test \
    python -m pytest -p no:cacheprovider "${TARGET[@]}" -v --tb=short
