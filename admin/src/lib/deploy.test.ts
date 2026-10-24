import { describe, it, expect } from 'vitest'
import { approveState, deployAction, isMyRequest, needsTestConfirm, rollbackState } from './deploy'

const base = { canDeploy: true, requiresApproval: false, version: 3, deployedVersion: 2 as number | null, pendingVersion: null as number | null }

describe('deployAction — 배포 버튼의 이름과 상태', () => {
  it('승인이 필요 없으면 「배포」', () => {
    expect(deployAction(base)).toMatchObject({ label: '배포', mode: 'deploy', disabled: false })
  })
  it('승인이 필요하면 「배포 요청」', () => {
    expect(deployAction({ ...base, requiresApproval: true })).toMatchObject({ label: '배포 요청', mode: 'request', disabled: false })
  })
  it('권한이 없으면 꺼 두고 이유를 준다', () => {
    const a = deployAction({ ...base, canDeploy: false })
    expect(a.disabled).toBe(true)
    expect(a.reason).toContain('관리자')
  })
  it('이미 배포 중인 버전은 누를 수 없다', () => {
    const a = deployAction({ ...base, version: 2 })
    expect(a).toMatchObject({ label: '배포 중', disabled: true })
    expect(a.reason).toContain('이미')
  })
  it('같은 버전의 대기 요청이 있으면 다시 요청할 수 없다, 다른 버전이면 새 요청(이전 것은 대체)', () => {
    expect(deployAction({ ...base, requiresApproval: true, pendingVersion: 3 })).toMatchObject({ disabled: true })
    expect(deployAction({ ...base, requiresApproval: true, pendingVersion: 1 })).toMatchObject({ disabled: false })
  })
})

describe('approveState — 승인 버튼', () => {
  it('권한이 있고 남의 요청이면 켠다', () => {
    expect(approveState({ canApprove: true, isMine: false })).toEqual({ disabled: false, reason: '' })
  })
  it('본인 요청이면 끄고 이유를 준다', () => {
    const a = approveState({ canApprove: true, isMine: true })
    expect(a.disabled).toBe(true)
    expect(a.reason).toContain('다른')
  })
  it('권한이 없으면 끄고 이유를 준다', () => {
    expect(approveState({ canApprove: false, isMine: false }).disabled).toBe(true)
  })
})

describe('isMyRequest', () => {
  it('요청자 이름이 내 이름 또는 이메일과 같으면 본인', () => {
    expect(isMyRequest('김관리', { name: '김관리', email: 'a@x.kr' })).toBe(true)
    expect(isMyRequest('a@x.kr', { name: '', email: 'a@x.kr' })).toBe(true)
    expect(isMyRequest('박둘째', { name: '김관리', email: 'a@x.kr' })).toBe(false)
    expect(isMyRequest(null, { name: '김관리', email: 'a@x.kr' })).toBe(false)
  })
})

describe('needsTestConfirm', () => {
  it('시험 대화가 없으면 확인을 한 번 더 받는다', () => {
    expect(needsTestConfirm(0)).toBe(true)
    expect(needsTestConfirm(2)).toBe(false)
  })
})

describe('rollbackState — 바로 앞 번호 버전으로', () => {
  const vs = [{ number: 3 }, { number: 2 }, { number: 1 }]
  it('배포 중 버전 앞에 버전이 있으면 그 번호', () => {
    expect(rollbackState(vs, 3)).toMatchObject({ target: 2, disabled: false })
  })
  it('배포 전이거나 앞선 버전이 없으면 끄고 이유', () => {
    expect(rollbackState(vs, null)).toMatchObject({ target: null, disabled: true })
    expect(rollbackState(vs, 1)).toMatchObject({ target: null, disabled: true })
    expect(rollbackState(vs, 1).reason).not.toBe('')
  })
})
