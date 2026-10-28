#!/usr/bin/env pwsh
# backend 단위/통합 테스트를 풀스택(db·redis·fake-llm)으로 돌린 뒤, 테스트 전용 인프라를 자동 중지한다.
#
# - 임베딩은 기본이 결정적 가짜다(fake-llm의 /v1/embeddings, #260). ollama는 뜨지 않는다.
# - 실제 bge-m3를 쓰는 `real_embedding` 마커 시험은 기본 실행에서 빠진다. 수동으로만:
#     $env:TEST_REAL_EMBEDDING='1'; ./scripts/test.ps1
# - 코드 변경 즉시 반영을 위해 호스트 backend/를 컨테이너에 마운트한다(rebuild 불필요).
# - 종료 시(성공/실패 무관) db/redis(/ollama) 테스트 컨테이너를 stop한다.
# - e2e 서비스(api-e2e 등)는 별도 워크플로우이므로 건드리지 않는다.
#
# 사용법:
#   ./scripts/test.ps1
#   ./scripts/test.ps1 tests/test_rag.py
#   ./scripts/test.ps1 tests/test_rag.py::test_pdf_normal_text_skips_ocr

$ErrorActionPreference = 'Continue'
Set-Location (Join-Path $PSScriptRoot '..')

$composeFiles = @('-f', 'docker-compose.test.yml')
$infra = @('test', 'db-test', 'redis-test', 'fake-llm')
$real = $env:TEST_REAL_EMBEDDING -eq '1'
$pytestMark = @()
if ($real) {
    $composeFiles += @('--profile', 'real-embedding')
    $infra += @('ollama-test', 'ollama-test-init')
    $pytestMark = @('-m', 'real_embedding')
    Write-Host '[test.ps1] 실제 bge-m3(real_embedding 마커)로 실행 - ollama를 띄운다' -ForegroundColor Cyan
}
# @(...)로 강제 배열화: 단일 인자일 때 if 블록이 스칼라로 풀려 @splat이 문자 단위로
# 쪼개지는 것을 막는다.
$default = if ($real) { 'tests/test_real_embedding.py' } else { 'tests/' }
$pytestTarget = if ($args.Count -gt 0) { @($args) } else { @($default) }
$code = 0

try {
    if ($real) {
        docker compose @composeFiles run --rm ollama-test-init
        if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE; return }
    }
    docker compose @composeFiles run --rm -v "$($PWD.Path)/backend:/app" test `
        python -m pytest -p no:cacheprovider @pytestMark @pytestTarget -v --tb=short
    $code = $LASTEXITCODE
}
finally {
    Write-Host "`n[test.ps1] 테스트 인프라 중지 중..." -ForegroundColor Cyan
    docker compose @composeFiles stop @infra | Out-Null
}
exit $code
