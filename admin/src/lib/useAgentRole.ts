import { useEffect, useSyncExternalStore } from 'react'
import { currentAgentRole } from '../api'
import { ensureRole, subscribeRole, type AgentRole } from '../roleStore'

// Admin 전용 화면이 쓰는 「내 역할」 훅(#120). 서버 역할(members/me)을 한 번 읽어 모든 화면이 공유한다.
// 읽기 전에는 null — `role === 'admin'` 판정이 거짓이라 쓰기 컨트롤이 숨은 채로 시작한다.
export function useAgentRole(): AgentRole | null {
  const role = useSyncExternalStore(subscribeRole, () => currentAgentRole())
  useEffect(() => { void ensureRole() }, [])
  return role
}
export function useIsAdmin(): boolean { return useAgentRole() === 'admin' }
