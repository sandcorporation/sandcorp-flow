// 운영 = production 브랜치 머리(#340). 이 잡은 production만 배포한다(잡 브랜치 지정자 */production).
//
//   main push → GitHub Actions release.yml: 검사(pr-check) → 이미지 셋(image.yml, ubuntu-24.04-arm) → production fast-forward
//   production push → 여기: 굽지도 검사하지도 않는다. ghcr.io/sandcorporation/sandcorp-flow-{api,admin,widget}:<sha>가 있는지 짧게 보고
//                     운영 호스트에서 pull → scripts/deploy.sh(무중단 롤링)
//
// 파이프라인은 Jenkins 공용 라이브러리 sandcorp(sandcorporation/jenkins-shared)의 sandcorpDeploy — README 「승격 브랜치에서 배포」.
// 이 저장소 사정은 docs/deployment.md. 파라미터:
//   BUILD_ON=jenkins  비상 길: Actions가 못 구울 때 Jenkins 노드(macos, 못 잡으면 home)에서 아래 images를 라이브러리 빌드 스크립트로
//                     굽고 배포한다(검사 없음). 이 길로 올린 sha는 production 머리 그대로다.
//   ROLLBACK_SHA      비상 되돌리기: 빌드 없이 그 sha의 이미지로 배포. production은 그대로라 다음 승격이 다시 앞으로 간다.
//                     정석은 main에 revert PR — docs/deployment.md 「배포 / 롤백」
//
// .env는 여기서 만들지 않는다. 운영 호스트의 잡 워크스페이스에 놓아 둔 것을 compose가 ./.env로 읽는다(deployDir 기본값).
@Library('sandcorp') _

String repo = 'ghcr.io/sandcorporation/sandcorp-flow'

sandcorpDeploy(
  name: 'Flow',
  // 이름·캐시(<repo>:buildcache)는 .github/workflows/image.yml의 matrix와 같다. dockerfile·context는 BUILD_ON=jenkins 길에서만 쓴다. envVar는 compose가 읽지 않는다 — compose는 IMAGE_TAG 하나로 셋을 고정한다.
  images: [
    [repo: "${repo}-api", envVar: 'FLOW_API_IMAGE', dockerfile: 'backend/Dockerfile', context: 'backend', cache: "${repo}-api:buildcache"],
    [repo: "${repo}-admin", envVar: 'FLOW_ADMIN_IMAGE', dockerfile: 'admin/Dockerfile', context: 'admin', cache: "${repo}-admin:buildcache"],
    [repo: "${repo}-widget", envVar: 'FLOW_WIDGET_IMAGE', dockerfile: 'widget/Dockerfile', context: 'widget', cache: "${repo}-widget:buildcache"],
  ],
  deployFrom: 'production',
  imageWaitHint: 'https://github.com/sandcorporation/sandcorp-flow/actions/workflows/release.yml',
  // 아래 둘은 BUILD_ON=jenkins 비상 길에서만 쓴다(sandcorp-authkit은 공개 저장소에서 git+https라 ssh가 필요 없다, #320).
  sourceUrl: 'https://github.com/sandcorporation/sandcorp-flow',
  builder: 'sandcorp-flow-images',
  composeProject: 'sandcorp-flow-prod',   // docker-compose.prod.oracle.yml의 name:과 같아야 한다(다르면 새 스택이 뜬다)
  composeFiles: ['docker-compose.prod.oracle.yml'],
  deployScript: 'scripts/deploy.sh',      // 무중단 롤링(docker rollout). 자동 롤백 때는 SANDCORP_ROLLBACK=1로 migrate를 건너뛴다
)
