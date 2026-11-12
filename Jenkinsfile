// 운영 = production 브랜치 머리(#340). 이 잡은 production만 배포한다(잡 브랜치 지정자 */production).
//
//   main push → GitHub Actions release.yml: 검사(pr-check) → 이미지 셋(image.yml, ubuntu-24.04-arm) → production fast-forward
//   production push → 여기: 굽지도 검사하지도 않는다. ghcr.io/sandcorporation/sandcorp-flow-{api,admin,widget}:<sha>가 있는지 짧게 보고
//                     운영 호스트에서 pull → scripts/deploy.sh(무중단 롤링)
//
// 파이프라인은 Jenkins 공용 라이브러리 sandcorp(sandcorporation/jenkins-shared)의 sandcorpDeploy — README 「승격 브랜치에서 배포」.
// 이 저장소 사정은 docs/deployment.md. 파라미터:
//   ROLLBACK_SHA      비상 되돌리기: 빌드 없이 그 sha의 이미지로 배포. production은 그대로라 다음 승격이 다시 앞으로 간다.
//                     정석은 main에 revert PR — docs/deployment.md 「배포 / 롤백」
//
// .env는 여기서 만들지 않는다. 운영 호스트의 잡 워크스페이스에 놓아 둔 것을 compose가 ./.env로 읽는다(deployDir 기본값).
@Library('sandcorp') _

// sandcorpDeploy exposes BUILD_ON=jenkins for older adopters. Flow is deploy-only:
// fail closed if that shared-library option is selected or left over from an old job.
if (params.BUILD_ON == 'jenkins') {
  error('Flow 이미지 빌드는 GitHub Actions 전용입니다. release.yml을 다시 실행하세요.')
}

String repo = 'ghcr.io/sandcorporation/sandcorp-flow'

sandcorpDeploy(
  name: 'Flow',
  // Actions가 올린 태그를 확인하고 배포하는 데 필요한 이미지 이름만 둔다.
  images: [
    [repo: "${repo}-api", envVar: 'FLOW_API_IMAGE'],
    [repo: "${repo}-admin", envVar: 'FLOW_ADMIN_IMAGE'],
    [repo: "${repo}-widget", envVar: 'FLOW_WIDGET_IMAGE'],
  ],
  deployFrom: 'production',
  imageWaitHint: 'https://github.com/sandcorporation/sandcorp-flow/actions/workflows/release.yml',
  composeProject: 'sandcorp-flow-prod',   // docker-compose.prod.oracle.yml의 name:과 같아야 한다(다르면 새 스택이 뜬다)
  composeFiles: ['docker-compose.prod.oracle.yml'],
  deployScript: 'scripts/deploy.sh',      // 무중단 롤링(docker rollout). 자동 롤백 때는 SANDCORP_ROLLBACK=1로 migrate를 건너뛴다
)
