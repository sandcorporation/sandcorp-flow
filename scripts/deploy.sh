#!/usr/bin/env bash
# oracle(Ampere A1) 무중단 배포 — Jenkinsfile의 「배포」 단계가 oracle 노드에서 호출한다(ADR-0015·0036).
#
# 사용법(원격 A1에서): IMAGE_TAG=<git-sha> bash scripts/deploy.sh <git-sha>
#
# 전제(A1 호스트에 1회 준비):
#   - docker + compose v2 + `docker rollout` 플러그인(https://github.com/wowu/docker-rollout)
#   - `docker login ghcr.io`(read:packages PAT) — pull 인증, ~/.docker/config.json에 영속
#   - 배포 디렉터리 = Jenkins 잡 워크스페이스(파이프라인이 그 SHA를 체크아웃한 뒤 워크스페이스에서 부른다).
#     호스트에 /home/jenkins/agent/workspace → /var/jenkins_home/workspace 심볼릭 링크 전제(상대 bind mount). docs/deployment.md
#   - .env(SECRET_KEY·DB_PASSWORD·ALLOWED_HOSTS 등, 이름은 .env.prod.example) 호스트에 상주(이미지·git 제외)
#   - docker-compose.prod.oracle.yml(이미지 GHCR 참조 + ${IMAGE_TAG} 핀)
#
# 무중단 원리: stateless 앱(api·worker·worker-chat)만 `docker rollout`으로 start-first 교체.
# db·redis·nginx·events는 교체하지 않거나 짧은 재시작 허용(이벤트는 outbox/at-least-once
# 라 소비자 순간 단절을 버팀). 마이그레이션은 롤아웃 전에(expand/contract — 옛/새 코드 공존).
set -euo pipefail

GIT_SHA="${1:?usage: deploy.sh <git-sha>}"

if [ ! -f .env ]; then
  echo ".env가 없다. .env.prod.example을 복사해 채워 두어야 한다(docs/deployment.md)." >&2
  exit 1
fi
export IMAGE_TAG="$GIT_SHA"

# 플러그인이 없으면 `docker rollout --help`가 docker 일반 도움말을 exit 0으로 내므로 문구로 확인한다.
# migrate를 돌린 뒤 롤링 단계에서 멈추는 반쯤 배포를 막으려고 맨 앞에서 본다.
if ! docker rollout --help 2>&1 | grep -q 'Rollout new Compose'; then
  echo "docker rollout 플러그인이 없다(에이전트 ~/.docker/cli-plugins/docker-rollout). docs/deployment.md" >&2
  exit 1
fi

COMPOSE_FILE="docker-compose.prod.oracle.yml"
COMPOSE="docker compose -f $COMPOSE_FILE"

echo "▶ pull ($IMAGE_TAG)"
$COMPOSE pull

echo "▶ 데이터스토어 기동(마이그레이션 전제)"
$COMPOSE up -d db redis

echo "▶ migrate (롤아웃 전 — expand/contract). collectstatic은 STATIC_ROOT 미사용으로 불필요."
$COMPOSE run --rm api python manage.py migrate --noinput

echo "▶ 정적 SPA 갱신(init이 dist를 볼륨에 복사 후 종료)"
$COMPOSE up -d --no-deps widget-init admin-init

# --no-deps: depends_on로 api가 딸려 재생성돼 다운타임 나는 것을 막는다(api는 아래서 롤링).
# events = relay + 소비자 4개 한 프로세스(#290). --remove-orphans: compose 파일에서 빠진 서비스
# (옛 relay·worker-*-bridge·worker-webhook 컨테이너)를 새 events를 만들기 전에 지운다 — relay가
# 잠깐이라도 둘 뜨지 않게(outbox 순서). rollout 임시 컨테이너·run --rm은 orphan이 아니라 남는다.
echo "▶ 이벤트 파이프라인 기동/갱신"
$COMPOSE up -d --no-deps --remove-orphans events

# nginx 설정은 단일 파일 bind mount라 (1) compose는 파일 내용 변경으로 재생성하지 않고,
# (2) git reset --hard가 파일을 inode째 교체해 실행 중 컨테이너는 옛 inode를 계속 본다(reload·
# restart는 mount 유지라 stale, recreate만 현재 파일을 봄). 그래서 컨테이너가 서빙 중인 설정이
# 현재 파일과 다를 때만 재생성한다 — 설정 안 바뀐 배포는 nginx를 안 건드려 무중단, 바뀐 배포에서만
# ~1s 재생성(NPM 재시도로 흡수).
echo "▶ nginx 설정 변경 확인"
if ! $COMPOSE exec -T nginx cat /etc/nginx/nginx.conf 2>/dev/null \
     | cmp -s - backend/nginx/nginx.oracle.conf; then
  echo "  변경 감지 → nginx 재생성"
  $COMPOSE up -d --no-deps --force-recreate nginx
else
  echo "  변경 없음 → 유지(무중단)"
fi

echo "▶ 무중단 롤링(start-first → /api/health 통과 → 옛 컨테이너 드레인)"
for svc in api worker worker-chat; do
  docker rollout -t 180 -f "$COMPOSE_FILE" "$svc"
done

# 우리 nginx는 호스트 포트를 안 열고 NPM 뒤에 있으므로, api 컨테이너 내부에서 health를 친다.
echo "▶ 스모크(api health)"
$COMPOSE exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" \
  && echo "✔ deploy OK: $IMAGE_TAG"
