#!/usr/bin/env bash
# backend 단위/통합 테스트를 풀스택(db·redis·fake-llm)으로 돌린 뒤, 테스트 전용 인프라를 자동 중지한다.
#
# - 임베딩은 기본이 결정적 가짜다(fake-llm의 /v1/embeddings, #260). ollama는 뜨지 않는다.
# - 실제 bge-m3를 쓰는 `real_embedding` 마커 시험은 기본 실행에서 빠진다. 수동으로만:
#     TEST_REAL_EMBEDDING=1 bash scripts/test.sh
#   (ollama-test를 띄우고 bge-m3를 받은 뒤 마커 시험만 돈다. 첫 실행은 모델 내려받기로 오래 걸린다.)
# - 코드 변경 즉시 반영을 위해 호스트 backend/를 컨테이너에 마운트한다(rebuild 불필요).
#   리눅스에선 컨테이너를 호스트 uid:gid로 돌려, 마운트 안에 root 소유 파일(media/ 등)을 남기지 않는다.
# - 종료 시(성공/실패/중단 무관) db/redis(/ollama) 테스트 컨테이너를 stop한다.
#   stop이라 다음 실행은 start로 빠르게 재개된다(워밍업 회피).
# - e2e 서비스(api-e2e 등)는 별도 워크플로우이므로 건드리지 않는다.
#
# 사용법:
#   bash scripts/test.sh
#   bash scripts/test.sh tests/test_rag.py
#   bash scripts/test.sh tests/test_rag.py::test_pdf_normal_text_skips_ocr
#   TEST_REAL_EMBEDDING=1 bash scripts/test.sh
set -uo pipefail
cd "$(dirname "$0")/.."

COMPOSE_FILES=(-f docker-compose.test.yml)
INFRA=(test db-test redis-test fake-llm)
PYTEST_MARK=()
if [ "${TEST_REAL_EMBEDDING:-0}" = "1" ]; then
    COMPOSE_FILES+=(--profile real-embedding)
    INFRA+=(ollama-test ollama-test-init)
    PYTEST_MARK=(-m real_embedding)
    TARGET=("${@:-tests/test_real_embedding.py}")
    echo "[test.sh] 실제 bge-m3(real_embedding 마커)로 실행 — ollama를 띄운다" >&2
else
    TARGET=("${@:-tests/}")
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

if [ "${TEST_REAL_EMBEDDING:-0}" = "1" ]; then
    # bge-m3 내려받기가 끝날 때까지 기다린다(이미 받았으면 바로 끝난다).
    MSYS_NO_PATHCONV=1 docker compose "${COMPOSE_FILES[@]}" run --rm ollama-test-init || exit 1
fi

# fake-llm 이미지는 app.py를 구워 넣는다 — 바뀐 것이 있으면 다시 빌드한다(캐시가 맞으면 즉시 끝난다).
MSYS_NO_PATHCONV=1 docker compose "${COMPOSE_FILES[@]}" build fake-llm >/dev/null || exit 1

MSYS_NO_PATHCONV=1 docker compose "${COMPOSE_FILES[@]}" run --rm \
    "${RUN_USER[@]}" -e TEST_CPU_LIMIT -v "${HOST_BACKEND}:/app" test \
    python -m pytest -p no:cacheprovider "${PYTEST_MARK[@]+"${PYTEST_MARK[@]}"}" "${TARGET[@]}" -v --tb=short --junitxml=/app/.junit/backend.xml
