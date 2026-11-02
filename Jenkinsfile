// main 배포 — 파이프라인은 Jenkins 공용 라이브러리 sandcorp(sandcorporation/jenkins-shared)의 sandcorpDeploy에 있다(jenkins-shared #9).
// 여기에는 이 저장소가 넘기는 값만 둔다. 동작(작업 브랜치 push 무시·이미 배포한 sha 건너뜀·배포 전 검사(PR 검사가 같은 트리를
// 통과시켰으면 생략)·mac 우선 빌드(3분 못 잡으면 home)·oracle pull 배포·실패 시 직전 이미지로 자동 롤백·디스코드 알림)과
// 인자·잡 파라미터(BUILDER·MAC_WAIT_SECONDS·ROLLBACK_SHA)는 jenkins-shared README, 이 저장소 사정은 docs/deployment.md.
//
// .env는 여기서 만들지 않는다. oracle 잡 워크스페이스(/home/jenkins/agent/workspace/sandcorp-flow/.env)에 상주한다(deployDir 기본값).
@Library('sandcorp') _

String repo = 'ghcr.io/sandcorporation/sandcorp-flow'

sandcorpDeploy(
  name: 'Flow',
  // 캐시는 전환 전 주소(<repo>:buildcache)를 이어 쓴다. envVar는 compose가 읽지 않는다 — compose는 IMAGE_TAG 하나로 셋을 고정한다.
  images: [
    [repo: "${repo}-api", envVar: 'FLOW_API_IMAGE', dockerfile: 'backend/Dockerfile', context: 'backend', cache: "${repo}-api:buildcache"],
    [repo: "${repo}-admin", envVar: 'FLOW_ADMIN_IMAGE', dockerfile: 'admin/Dockerfile', context: 'admin', cache: "${repo}-admin:buildcache"],
    [repo: "${repo}-widget", envVar: 'FLOW_WIDGET_IMAGE', dockerfile: 'widget/Dockerfile', context: 'widget', cache: "${repo}-widget:buildcache"],
  ],
  sourceUrl: 'https://github.com/sandcorporation/sandcorp-flow',
  buildSsh: true,                         // api가 sandcorp-authkit(비공개 git 의존성)을 받는다
  builder: 'sandcorp-flow-images',        // 전환 전 buildx 빌더를 그대로 쓴다
  check: 'bash scripts/ci/check.sh',      // Jenkinsfile.pr과 같은 것. 실패하면 굽기를 끊고 배포하지 않는다
  composeProject: 'sandcorp-flow-prod',   // docker-compose.prod.oracle.yml의 name:과 같아야 한다(다르면 새 스택이 뜬다)
  composeFiles: ['docker-compose.prod.oracle.yml'],
  deployScript: 'scripts/deploy.sh',      // 무중단 롤링(docker rollout). 자동 롤백 때는 SANDCORP_ROLLBACK=1로 migrate를 건너뛴다
)
