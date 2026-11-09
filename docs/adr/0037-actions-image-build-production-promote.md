# ADR-0037: 이미지는 Actions가 굽고 production 브랜치로 승격한다 — Jenkins는 배포만

## Status
Accepted (2026-11-09, #340). [ADR-0036](0036-image-build-on-jenkins.md)(Jenkins에서 굽기)을 대체한다. 본보기는 sandcorp-metrics #32.

## Context
ADR-0036 뒤 main push마다 Jenkins 잡 `sandcorp-flow`가 배포 전 검사(`scripts/ci/check.sh`)와 이미지 셋(api·admin·widget) 굽기를
mac(못 잡으면 home, QEMU)에서 하고 oracle에 배포했다. 배포 경로에 mac 디스크·메모리 부족, 공유 노드의 e2e 3초 한도 흔들림이 섞여
배포가 검사 탓에 깨졌다(배포 #34). 한편 PR 검사는 이미 GitHub 호스팅 Actions로 옮겼고(#317·#320), sandcorp-authkit이
공개 저장소에서 git+https로 받아져 이미지 빌드에 자격이 필요 없어졌다. 저장소가 공개라 GitHub 호스팅 arm64 실행기(`ubuntu-24.04-arm`)를 쓸 수 있다.

## Decision
- `release.yml`(on push main): `check`(pr-check.yml을 workflow_call) → `image`(image.yml, 이미지마다 matrix 잡, arm64 네이티브,
  GHCR `<이미지>:<sha>`, 레지스트리 캐시 `:buildcache`) → `promote`(`scripts/ci/promote.sh ff <sha>` — `production` fast-forward만).
- **운영 = `production` 브랜치 머리.** Jenkins 잡은 `*/production`만 보고 `sandcorpDeploy(deployFrom: 'production')`로
  검사·굽기 없이 이미지가 GHCR에 있는지만 확인하고 배포한다.
- 이미지 이름·태그·라벨·플랫폼·캐시 주소는 Jenkins가 굽던 것과 같다 — 운영 compose(`IMAGE_TAG` 하나로 셋 고정)는 그대로.
- 권한: 워크플로 기본 없음, check `contents: read`, image `packages: write`, promote `contents: write`. GITHUB_TOKEN 외 secrets 없음,
  `pull_request_target` 없음, 액션 SHA 고정.
- pr-check.yml은 `push: main`을 빼고 `workflow_call`을 받는다. main 캐시(gha) 저장 조건은 `github.event_name != 'pull_request'` —
  workflow_call에서 event_name은 부른 쪽의 `push`라 release의 check가 저장한다(PR은 복원만).
- 되돌리기: 정석은 main에 revert PR(같은 흐름으로 승격). 급하면 Jenkins `ROLLBACK_SHA`(production은 그대로).
  production을 옛 sha로 강제로 옮기는 워크플로는 두지 않는다 — ruleset이 강제 push·삭제를 막고, 조직 ruleset은 Actions를 bypass로 받지 않는다.
- 비상 길: Actions가 못 구우면 Jenkins `BUILD_ON=jenkins`가 Jenkinsfile `images`를 라이브러리 빌드 스크립트로 굽는다(검사 없음).

## Consequences
- 배포 경로에서 검사·굽기가 빠진다(metrics 실측: 병합→승격 1분 28초, Jenkins 배포 49.5초). flow는 검사가 길어(e2e 2조각·backend 약 3분)
  병합→승격이 10분 남짓이다 — `wait-deploy.py`는 `--start-timeout 30`으로 기다린다.
- release가 깨지면 배포가 없다(production이 그대로). 깨진 main은 고친 PR로 앞으로 간다.
- GHCR 패키지는 GITHUB_TOKEN이 쓸 수 있어야 한다. Jenkins 개인 토큰이 만든 옛 패키지는 지우고 Actions가 다시 만든다(저장소에 연결, 공개).
- 승격이 겹치지 않게 promote 잡은 `production-branch` 동시성 그룹으로 줄 세우고, 늦게 끝난 옛 실행은 건너뛴다.
