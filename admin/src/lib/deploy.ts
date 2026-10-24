// 버전·배포 화면의 순수 규칙. 서버가 최종 판정을 하고, 여기서는 버튼의 이름·꺼짐·이유만 정한다(ADR-0027).

export type DeployMode = 'deploy' | 'request'
export type ButtonState = { disabled: boolean; reason: string }

export function deployAction(i: {
  canDeploy: boolean
  requiresApproval: boolean
  version: number
  deployedVersion: number | null
  pendingVersion: number | null
}): ButtonState & { label: string; mode: DeployMode } {
  const mode: DeployMode = i.requiresApproval ? 'request' : 'deploy'
  const label = mode === 'request' ? '배포 요청' : '배포'
  if (!i.canDeploy) return { label, mode, disabled: true, reason: '배포는 관리자만 할 수 있습니다.' }
  if (i.version === i.deployedVersion) return { label: '배포 중', mode, disabled: true, reason: '이미 배포 중인 버전입니다.' }
  if (mode === 'request' && i.pendingVersion === i.version) return { label, mode, disabled: true, reason: '이 버전은 이미 승인 대기 중입니다.' }
  return { label, mode, disabled: false, reason: '' }
}

export function approveState(i: { canApprove: boolean; isMine: boolean }): ButtonState {
  if (!i.canApprove) return { disabled: true, reason: '승인은 관리자만 할 수 있습니다.' }
  if (i.isMine) return { disabled: true, reason: '본인이 올린 요청은 다른 관리자가 승인해야 합니다.' }
  return { disabled: false, reason: '' }
}

/** 요청 응답은 요청자의 표시 이름(이름 없으면 이메일)만 준다. 같으면 본인 요청으로 본다 — 서버가 다시 막는다(self_approval). */
export function isMyRequest(requestedByName: string | null | undefined, me: { name: string; email: string }): boolean {
  if (!requestedByName) return false
  return requestedByName === (me.name || me.email)
}

/** 시험 대화를 한 번도 하지 않고 배포하려 하면 한 번 더 확인한다. */
export function needsTestConfirm(testedTurns: number): boolean {
  return testedTurns === 0
}

export function rollbackState(versions: { number: number }[], deployedVersion: number | null): ButtonState & { target: number | null } {
  if (deployedVersion === null) return { target: null, disabled: true, reason: '배포 중인 버전이 없어 되돌릴 수 없습니다.' }
  const target = versions.map((v) => v.number).filter((n) => n < deployedVersion).sort((a, b) => b - a)[0] ?? null
  if (target === null) return { target: null, disabled: true, reason: `v${deployedVersion}보다 앞선 버전이 없습니다.` }
  return { target, disabled: false, reason: '' }
}
