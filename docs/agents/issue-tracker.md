# Issue tracker: GitHub Issues

이슈와 스펙(PRD)은 GitHub `sandcorporation/sandcorp-flow` 이슈로 관리한다(2026-10-24 결정). 예전 `.scratch/` 로컬 마크다운은 Embed Chat 시절 기록으로만 남는다.

## Conventions

- 스펙(PRD)은 우산 이슈 하나(제목 `[스펙] …`, 라벨 `spec`), 구현 티켓은 그 아래 하위 이슈(sub-issue)로 달고 GitHub 마일스톤(M0~M4…)으로 묶는다.
- 막힘 관계는 본문 첫머리의 `막힘: #N` 줄과 GitHub의 blocked-by 관계로 함께 적는다.
- 분류 상태는 라벨로 적는다(`triage-labels.md`). 맡길 수 있는 티켓만 `ready-for-agent`, 다른 이슈·외부 작업을 기다리면 `blocked`.
- 대화·결정·넘김은 이슈 댓글로 남긴다. 이슈·PR·댓글은 한국어로 쓴다.

## When a skill says "publish to the issue tracker"

`gh issue create`로 이슈를 만든다(필요한 라벨을 붙이고, 스펙 아래면 하위 이슈로 연결).

## When a skill says "fetch the relevant ticket"

`gh issue view <번호> --comments`로 본문과 댓글을 읽는다.
