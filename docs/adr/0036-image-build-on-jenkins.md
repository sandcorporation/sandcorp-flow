# ADR-0036: 운영 이미지를 Jenkins에서 굽는다 — mac 우선, 대체 home, 같은 잡이 oracle 배포

> 갱신(2026-11-02, jenkins-shared #9): 파이프라인을 공용 라이브러리 `sandcorpDeploy`로 옮겼다. `scripts/ci/build-images.sh`는 없어지고
> 라이브러리 `build-image.sh`가 이미지마다 굽는다. `:latest`는 더 올리지 않는다(캐시 `:buildcache`·sha 태그는 그대로). 지금 흐름은 [deployment.md](../deployment.md).

## Status
Accepted (2026-11-02, #293). [ADR-0015](0015-oracle-zero-downtime-rolling-deploy.md)의 빌드·트리거 부분(Q6·Q7)을 대체한다.

## Context
대표 결정으로 GitHub Actions self-hosted 러너를 더 쓰지 않는다. 그 러너를 쓰던 것은 `.github/workflows/build-images.yml`
하나였다. main push → home(옛 이름 sub) 러너 3대가 QEMU buildx로 linux/arm64 이미지 3개(api·admin·widget)를 굽고
ghcr에 `<sha>`·`latest`로 올린 뒤 `release` 브랜치를 그 SHA로 갱신하면, 그 push 웹훅으로 Jenkins freestyle 잡
`sandcorp-flow`가 oracle에서 `scripts/deploy.sh`를 돌렸다.

portfolio(#165)와 AI-Trend-News(#142)는 이미 같은 일을 Jenkins 파이프라인 한 잡으로 한다: main push → mac(`macos` 노드,
arm64 네이티브)에서 굽고, 3분 안에 실행기를 못 잡으면 home(built-in, QEMU)에서 굽고 → 같은 잡의 다음 단계가 oracle에서
pull·교체만 한다.

## Decision
본보기와 같은 모양으로 옮긴다.

- **한 잡, 한 트리거**: Jenkins 잡 `sandcorp-flow`를 파이프라인(SCM의 `Jenkinsfile`, 브랜치 `main`)으로 바꾼다. main push
  웹훅 하나가 빌드와 배포를 차례로 돌린다. `release` 브랜치는 더 쓰지 않는다.
- **배포 순서 보장**: 「이미지 빌드」 단계가 세 이미지를 모두 올리고 0으로 끝나야 「배포」 단계가 돈다(선언형 파이프라인의
  단계 순서). 하나라도 실패하면 배포 단계는 돌지 않는다.
- **빌드 노드**: `BUILDER=auto`(기본)는 macos를 먼저, `MAC_WAIT_SECONDS`(180초) 안에 못 잡으면 home(`built-in`). 넘어가는
  것은 노드를 못 잡았을 때뿐이고, 잡은 뒤의 실패는 그대로 실패다. 세 이미지는 한 노드에서 차례로 굽는다
  (`scripts/ci/build-images.sh`) — mac은 네이티브라 빠르고, 실행기가 하나라 나눠도 병렬이 되지 않는다.
- **태그·캐시는 그대로**: `ghcr.io/sandcorporation/sandcorp-flow-<image>:<sha>`와 `:latest`, 캐시 `:buildcache`(registry,
  `mode=max`). oracle의 compose·`deploy.sh`는 바꾸지 않는다.
- **자격은 기존 것을 다시 쓴다**: ghcr 쓰기 `ghcr-push-token`, 비공개 sandcorp-auth 읽기 SSH `<github-ssh-credentials-id>`(portfolio가
  sandcorp-authkit을 받는 것과 같은 키, api 빌드에만 BuildKit `--ssh default`), 알림 `discord-deploy-webhook`. GitHub
  secret `AUTHKIT_SSH_KEY`는 더 쓰지 않는다.
  (2026-11-09, #320: sandcorp-auth가 공개돼 authkit을 git+https로 받는다 — 이 SSH 키·`--ssh default`는 더 필요 없다.)
- **롤백**: `release` 되감기 대신 파라미터 `ROLLBACK_SHA` — 빌드를 건너뛰고 그 sha 이미지로 배포 단계만 돈다.

## Considered Options
- **release 브랜치를 남기고 빌드만 Jenkins로**(새 빌드 잡이 굽고 release를 push → 기존 freestyle 잡이 배포): 기각.
  잡이 둘, 트리거가 둘이고 Jenkins가 GitHub에 쓰는 자격이 더 필요하다. 본보기 저장소들과도 모양이 달라진다.
- **매트릭스처럼 세 이미지를 노드 셋에 나눠 병렬로**: 기각. mac 실행기가 하나이고, home은 QEMU라 셋을 동시에 돌리면
  오히려 느리다.

## Consequences
- Jenkins 잡 `sandcorp-flow`를 freestyle에서 파이프라인으로 바꾸는 일은 Jenkins 설정이라 사람이 한다. 초안은
  [docs/ops/jenkins-sandcorp-flow.config.xml](../ops/jenkins-sandcorp-flow.config.xml).
- 잡 이름이 같으니 oracle 워크스페이스(`/var/jenkins_home/workspace/sandcorp-flow`, 운영 `.env`·`media`)도 같다.
- 「CI 설정만 바꾼 커밋은 `[skip ci]`」 관례는 없어졌다 — main push는 모두 빌드·배포한다(빌드 캐시로 짧다).
- 저장소에 GitHub Actions 워크플로가 남지 않는다.
