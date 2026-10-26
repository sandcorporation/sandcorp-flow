import { Routes, Route, Navigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { FileText, Network, Users, Settings, UserCog, Headphones, Building2, BarChart3, Workflow } from 'lucide-react'
import OperatorLogin from './pages/OperatorLogin'
import OperatorTenants from './pages/OperatorTenants'
import TenantLogin from './pages/TenantLogin'
import TryPage from './pages/TryPage'
import TrySignupPage from './pages/TrySignupPage'
import TryClaimPage from './pages/TryClaimPage'
import TryFlowPage from './pages/TryFlowPage'
import PublicFlowPage from './pages/PublicFlowPage'
import TrialBanner from './components/TrialBanner'
import DashboardLayout, { NavItem } from './components/DashboardLayout'
import DocumentsTab from './components/DocumentsTab'
import KnowledgeGraphTab from './components/KnowledgeGraphTab'
import VisitorsTab from './components/VisitorsTab'
import SessionDetailPage from './components/SessionDetailPage'
import ConfigTab from './components/ConfigTab'
import AgentsTab from './components/AgentsTab'
import HitlTab from './components/HitlTab'
import FlowsTab from './components/FlowsTab'
import FlowEditorPage from './components/FlowEditorPage'
import TenantUsageTab from './components/TenantUsageTab'
import OperatorUsageTab from './components/OperatorUsageTab'
import { getAccess, bootSilentRefresh } from './auth'
import { setRole } from './roleStore'
import { operatorLogout, operatorLogoutAll, agentLogout, agentLogoutAll, memberMe, memberLoginUrl, ACCOUNT_URL } from './api'

const ICON = 'h-4 w-4'
const operatorNav: NavItem[] = [
  { to: '/operator/tenants', label: 'Tenants', icon: <Building2 className={ICON} /> },
  { to: '/operator/usage', label: '사용량', icon: <BarChart3 className={ICON} /> },
]
const tenantNav: NavItem[] = [
  { to: '/tenant/documents', label: '문서', icon: <FileText className={ICON} /> },
  { to: '/tenant/graph', label: '지식그래프', icon: <Network className={ICON} /> },
  { to: '/tenant/visitors', label: 'Visitors', icon: <Users className={ICON} /> },
  { to: '/tenant/config', label: '설정', icon: <Settings className={ICON} /> },
  { to: '/tenant/agents', label: '팀원', icon: <UserCog className={ICON} /> },
  { to: '/tenant/flows', label: '흐름', icon: <Workflow className={ICON} /> },
  { to: '/tenant/hitl', label: 'HITL 상담', icon: <Headphones className={ICON} /> },
  { to: '/tenant/usage', label: '사용량', icon: <BarChart3 className={ICON} /> },
]

export default function App() {
  // Operator: access는 sessionStorage, refresh는 httpOnly 쿠키(ADR-0013).
  const [operatorAuthed, setOperatorAuthed] = useState(() => !!getAccess('operator'))
  const [operatorBooting, setOperatorBooting] = useState(true)

  // 멤버(고객, #40): operator와 동일하게 access=sessionStorage, refresh=httpOnly 쿠키.
  const [agentAuthed, setAgentAuthed] = useState(() => !!getAccess('agent'))
  const [agentBooting, setAgentBooting] = useState(true)
  const [agentUsername, setAgentUsername] = useState(() => localStorage.getItem('agent_username'))
  const [tenantName, setTenantName] = useState<string | null>(null)

  useEffect(() => {
    // 새로고침으로 sessionStorage access가 사라져도 refresh 쿠키로 무중단 복구.
    // sandcorp-auth 로그인 콜백(#21)이 심은 Operator refresh 쿠키도 여기서 access로 바뀐다.
    Promise.all([
      bootSilentRefresh('operator').then((ok) => setOperatorAuthed(ok)).finally(() => setOperatorBooting(false)),
      bootSilentRefresh('agent').then((ok) => setAgentAuthed(ok)).finally(() => setAgentBooting(false)),
    ])
  }, [])

  // 콜백으로 바로 들어오면 이름을 모른다 — 세션이 열리면 /me 로 조직 이름과 내 이름을 채운다.
  useEffect(() => {
    if (!agentAuthed) return
    memberMe()
      .then((me) => {
        setTenantName(me.tenant_name)
        if (me.role === 'admin' || me.role === 'member') setRole(me.role)
        const who = me.name || me.email
        if (who) { localStorage.setItem('agent_username', who); setAgentUsername(who) }
      })
      .catch(() => {})
  }, [agentAuthed])

  const handleOperatorLogout = async () => {
    await operatorLogout()
    setOperatorAuthed(false)
  }
  const handleOperatorLogoutAll = async () => {
    await operatorLogoutAll()
    setOperatorAuthed(false)
  }

  const handleTenantLogin = (username: string) => {
    localStorage.setItem('agent_username', username)
    setAgentUsername(username)
    setAgentAuthed(true)
  }
  const endAgentSession = () => {
    setRole(null)
    localStorage.removeItem('agent_username')
    setAgentAuthed(false)
    setAgentUsername(null)
    setTenantName(null)
  }
  // 조직 바꾸기: 이 세션을 닫고 고객 진입으로 다시 간다. sandcorp 계정 세션이 살아 있어 곧장 조직 선택으로 돌아온다.
  const handleTenantSwitch = async () => {
    await agentLogout()
    endAgentSession()
    window.location.assign(memberLoginUrl())
  }
  const handleTenantLogout = async () => {
    await agentLogout()
    endAgentSession()
  }
  const handleTenantLogoutAll = async () => {
    await agentLogoutAll()
    endAgentSession()
  }

  // 인증 게이트: booting이면 null, 미인증이면 로그인(셸 없음), 인증되면 셸(Outlet으로 섹션 렌더).
  // 부팅 중에도 null이 아닌 요소를 둔다 — null이면 라우터가 Outlet을 그려 index의 Navigate가
  // 먼저 돌고, 콜백이 붙여 보낸 ?login_error가 사라진다(#21).
  const operatorEl = operatorBooting
    ? <div aria-busy="true" />
    : operatorAuthed
      ? <DashboardLayout brand="Operator" navItems={operatorNav} onLogout={handleOperatorLogout} onLogoutAll={handleOperatorLogoutAll} />
      : <OperatorLogin />

  const tenantEl = agentBooting
    ? <div aria-busy="true" />
    : agentAuthed
      ? <DashboardLayout brand={tenantName ?? '내 조직'} navItems={tenantNav} user={agentUsername} accountHref={ACCOUNT_URL} onSwitch={handleTenantSwitch} onLogout={handleTenantLogout} onLogoutAll={handleTenantLogoutAll} banner={<TrialBanner />} />
      : <TenantLogin onLogin={handleTenantLogin} />

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/operator" replace />} />

      {/* 로그인 없는 체험(#37): 인증 게이트·대시보드 셸 밖이다 */}
      <Route path="/try" element={<TryPage />} />
      <Route path="/try/flow/:id" element={<TryFlowPage />} />
      {/* 「이 흐름으로 가입」(#42): 고객 로그인 뒤 체험 사본을 내 테넌트로 옮긴다 */}
      {/* 세션 없이 「이 흐름으로 가입」(#198): 세션을 만든 뒤 로그인으로 — 색인 제외 */}
      <Route path="/try/signup" element={<TrySignupPage />} />
      <Route path="/try/claim" element={<TryClaimPage onLogin={handleTenantLogin} />} />
      <Route path="/try/view/:id" element={<TryFlowPage view />} />
      {/* 공개 흐름 보기(#39): 테넌트가 켠 공개 흐름의 배포 버전, 읽기 전용(:id = 테넌트 slug) */}
      <Route path="/flows/:id" element={<PublicFlowPage />} />

      <Route path="/operator" element={operatorEl}>
        <Route index element={<Navigate to="tenants" replace />} />
        <Route path="tenants" element={<OperatorTenants />} />
        <Route path="usage" element={<OperatorUsageTab />} />
      </Route>

      <Route path="/tenant" element={tenantEl}>
        <Route index element={<Navigate to="documents" replace />} />
        <Route path="documents" element={<DocumentsTab />} />
        <Route path="graph" element={<KnowledgeGraphTab />} />
        <Route path="visitors" element={<VisitorsTab />} />
        <Route path="visitors/:visitorId" element={<VisitorsTab />} />
        <Route path="sessions/:sessionId" element={<SessionDetailPage />} />
        <Route path="config" element={<ConfigTab />} />
        <Route path="agents" element={<AgentsTab />} />
        <Route path="hitl" element={<HitlTab />} />
        <Route path="hitl/:escalationId" element={<HitlTab />} />
        <Route path="flows" element={<FlowsTab />} />
        <Route path="flows/:id" element={<FlowEditorPage />} />
        <Route path="usage" element={<TenantUsageTab />} />
      </Route>
    </Routes>
  )
}
