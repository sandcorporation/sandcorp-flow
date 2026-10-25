#!/usr/bin/env bash
# e2e 전체 판 부하 재현(#177). 판마다 따로 폴더에 남긴다:
#   api.log(계측 줄 포함)·worker.log·chat-worker.log·pw.log·pw.json·stats.tsv(컨테이너 CPU)·pg.tsv(DB 대기)·summary.txt
# 쓰임: bash scripts/e2e-soak/soak.sh [판 수=3] [출력 폴더=.e2e-soak]
#   환경 변수: E2E_API_WORKERS(api 워커 수, 기본 2), PW_ARGS(playwright 추가 인자, 예: "--workers=4")
#   SOAK_BUILD=0 이면 이미지 빌드를 건너뛴다.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUNS="${1:-3}"
OUT="$(mkdir -p "${2:-$ROOT/.e2e-soak}" && cd "${2:-$ROOT/.e2e-soak}" && pwd)"
DC=(docker compose -f "$ROOT/docker-compose.test.yml" -f "$ROOT/scripts/e2e-soak/docker-compose.soak.yml")
PROJECT=sandcorp-flow-test

if [ -n "$(docker ps -q --filter name=$PROJECT)" ]; then
  echo "시험 인프라($PROJECT)가 이미 돌고 있다 — 다른 작업이 끝난 뒤 다시." >&2; exit 2
fi
if [ "${SOAK_BUILD:-1}" != 0 ]; then
  "${DC[@]}" build >"$OUT/build.log" 2>&1 || { echo "빌드 실패: $OUT/build.log" >&2; exit 1; }
fi

sample_stats() {  # 2초마다 컨테이너 CPU·메모리
  while :; do
    docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null \
      | grep "$PROJECT" | sed "s/^/$(date +%s.%N | cut -c1-14)\t/"
    sleep 2
  done
}
sample_pg() {  # 2초마다 DB 연결 상태·잠금 대기 수
  while :; do
    "${DC[@]}" exec -T db-test psql -U postgres -d sandcorp_flow -At -F $'\t' -c \
      "select extract(epoch from now()), count(*) filter (where state='active'), count(*) filter (where wait_event_type='Lock'), count(*) from pg_stat_activity where datname='sandcorp_flow'" 2>/dev/null
    sleep 2
  done
}

for i in $(seq 1 "$RUNS"); do
  RUN="$OUT/run-$(date +%Y%m%d-%H%M%S)-$i"; mkdir -p "$RUN"
  echo "== 판 $i/$RUNS → $RUN (api 워커 ${E2E_API_WORKERS:-2}, PW_ARGS='${PW_ARGS:-}')"
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1
  sample_stats >"$RUN/stats.tsv" & S1=$!
  sample_pg >"$RUN/pg.tsv" & S2=$!
  START=$(date +%s)
  # shellcheck disable=SC2086
  "${DC[@]}" run --rm -v "$RUN:/out" -e PLAYWRIGHT_JSON_OUTPUT_NAME=/out/pw.json e2e \
    npx playwright test --reporter=list,json ${PW_ARGS:-} >"$RUN/pw.log" 2>&1
  echo "exit=$? secs=$(( $(date +%s) - START ))" >"$RUN/exit.txt"
  kill "$S1" "$S2" 2>/dev/null; wait "$S1" "$S2" 2>/dev/null
  "${DC[@]}" logs --no-color api-e2e >"$RUN/api.log" 2>&1
  "${DC[@]}" logs --no-color --timestamps worker-e2e >"$RUN/worker.log" 2>&1
  "${DC[@]}" logs --no-color --timestamps chat-worker-e2e >"$RUN/chat-worker.log" 2>&1
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1
  python3 "$ROOT/scripts/e2e-soak/analyze.py" "$RUN" | tee "$RUN/summary.txt"
done
python3 "$ROOT/scripts/e2e-soak/analyze.py" --table "$OUT"/run-*
