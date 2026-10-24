#!/usr/bin/env pwsh
# backend 단위/통합 테스트를 풀스택(db·redis·ollama)으로 돌린 뒤, 테스트 전용 인프라를 자동 중지한다.
#
# - 기본 구성은 GPU 없이 돈다(ollama CPU). `docker info`에 nvidia 런타임이 있으면
#   docker-compose.test.gpu.yml을 겹쳐 ollama를 GPU로 돈다. $env:TEST_GPU='0'/'1'로 강제.
# - 코드 변경 즉시 반영을 위해 호스트 backend/를 컨테이너에 마운트한다(rebuild 불필요).
# - 종료 시(성공/실패 무관) db/redis/ollama 테스트 컨테이너를 stop하여
#   메모리(GPU면 VRAM)를 돌려준다. stop이라 다음 실행은 start로 빠르게 재개된다(워밍업 회피).
# - e2e 서비스(api-e2e 등)는 별도 워크플로우이므로 건드리지 않는다.
#
# 사용법:
#   ./scripts/test.ps1
#   ./scripts/test.ps1 tests/test_rag.py
#   ./scripts/test.ps1 tests/test_rag.py::test_pdf_normal_text_skips_ocr

$ErrorActionPreference = 'Continue'
Set-Location (Join-Path $PSScriptRoot '..')

$composeFiles = @('-f', 'docker-compose.test.yml')
$gpu = $env:TEST_GPU
if (-not $gpu) {
    $runtimes = docker info --format '{{json .Runtimes}}' 2>$null
    $gpu = if ("$runtimes" -match 'nvidia') { '1' } else { '0' }
}
if ($gpu -eq '1') { $composeFiles += @('-f', 'docker-compose.test.gpu.yml') }
$infra = @('test', 'db-test', 'redis-test', 'ollama-test', 'ollama-test-init')
# @(...)로 강제 배열화: 단일 인자일 때 if 블록이 스칼라로 풀려 @splat이 문자 단위로
# 쪼개지는 것을 막는다.
$pytestTarget = if ($args.Count -gt 0) { @($args) } else { @('tests/') }
$code = 0

try {
    docker compose @composeFiles run --rm -v "$($PWD.Path)/backend:/app" test `
        python -m pytest -p no:cacheprovider @pytestTarget -v --tb=short
    $code = $LASTEXITCODE
}
finally {
    Write-Host "`n[test.ps1] 테스트 인프라 중지 중(메모리 해제)..." -ForegroundColor Cyan
    docker compose @composeFiles stop @infra | Out-Null
}
exit $code
