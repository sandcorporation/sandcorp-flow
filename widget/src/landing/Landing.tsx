import ChatDemo from './ChatDemo'
import KGDemo from './KGDemo'
import SiteFooter from './SiteFooter'

const FEATURES = [
  { title: '지식그래프 기반 RAG', desc: '문서를 Entity·관계 그래프로 구조화해 더 정확한 근거로 답합니다.' },
  { title: '실시간 토큰 스트리밍', desc: 'AI 답변이 타이핑되듯 토큰 단위로 실시간 흘러나옵니다.' },
  { title: 'HITL 상담 전환', desc: 'AI가 불확실하거나 방문자가 원하면 사람 상담원으로 매끄럽게 전환합니다.' },
  { title: '임베드 위젯', desc: '한 줄 스니펫으로 어떤 웹사이트에도 챗봇을 삽입합니다.' },
]

const MailIcon = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="m3 7 9 6 9-6" />
  </svg>
)

const PhoneIcon = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />
  </svg>
)

export default function Landing() {
  return (
    <div className="landing">
      <section className="container hero">
        <div className="hero-copy">
          <div className="eyebrow">SANDCORP FLOW</div>
          <h1>지식그래프 기반<br />AI 챗봇 플랫폼</h1>
          <p>
            문서를 지식그래프로 구조화해 더 정확하게 답하고, 실시간 스트리밍으로 응답하며,
            필요할 땐 사람 상담원으로 전환합니다. 한 줄이면 어떤 사이트에도 삽입됩니다.
          </p>
          <div className="hero-cta">
            <a className="btn-primary" href="/admin-ui/">운영자 로그인</a>
            <a className="btn-secondary" href="/admin-ui/tenant">테넌트 로그인</a>
          </div>
        </div>
        <div className="hero-demo" data-testid="hero-demo">
          <ChatDemo />
        </div>
      </section>

      <section className="container section">
        <h2>핵심 기능</h2>
        <p className="sub">문서를 올리면 챗봇이 됩니다.</p>
        <div className="features">
          {FEATURES.map(f => (
            <div className="feature" key={f.title}>
              <h3>{f.title}</h3>
              <p>{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="container section">
        <h2>지식그래프</h2>
        <p className="sub">문서에서 추출한 Entity·관계를 시각화합니다. 노드를 눌러 펼쳐보세요.</p>
        <KGDemo />
      </section>

      <section className="container section">
        <h2>문의</h2>
        <p className="sub">도입·요금이 궁금하면 편하게 연락 주세요.</p>
        <div className="contact">
          <div className="contact-card">
            <span className="contact-icon" aria-hidden="true"><MailIcon /></span>
            <span className="contact-meta">
              <span className="contact-label">이메일</span>
              <a className="contact-value" href="mailto:gksdjf1690@gmail.com">gksdjf1690@gmail.com</a>
            </span>
          </div>
          <div className="contact-card">
            <span className="contact-icon" aria-hidden="true"><PhoneIcon /></span>
            <span className="contact-meta">
              <span className="contact-label">전화</span>
              <a className="contact-value" href="tel:01024831690">010-2483-1690</a>
            </span>
          </div>
        </div>
      </section>

      <SiteFooter />
    </div>
  )
}
