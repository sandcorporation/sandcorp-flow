#!/usr/bin/env bash
# Guard Flow against reintroducing Jenkins CI, PR, or image-build paths.
set -euo pipefail
cd "$(dirname "$0")/../.."

grep -Fq "if (params.BUILD_ON == 'jenkins')" Jenkinsfile
grep -Fq "deployFrom: 'production'" Jenkinsfile
grep -Fq "imageWaitHint:" Jenkinsfile
! grep -Eq 'dockerfile:|context:|cache:|sourceUrl:|builder:|buildSsh:|check:' Jenkinsfile
! grep -Eq 'BUILD_ON=jenkins|BUILDER=|MAC_WAIT_SECONDS=' Jenkinsfile
test ! -e Jenkinsfile.pr
test ! -e docs/ops/jenkins-sandcorp-flow-pr.config.xml
grep -Fq '<name>*/production</name>' docs/ops/jenkins-sandcorp-flow.config.xml
grep -Fq 'runs-on: ubuntu-24.04-arm' .github/workflows/image.yml
! grep -Eq 'runs-on:.*(self-hosted|macos)' .github/workflows/*.yml
! grep -Eq 'SANDCORP_BUILT_ON=home|on_home|건너뜀\(home' scripts/ci/check.sh

echo 'Actions-only CI/image build and deploy-only Jenkins contract OK'
