import SiteFooter from './SiteFooter'
import FlowMark from './FlowMark'
import ThemeToggle from './ThemeToggle'

// 랜딩: 흐름 챗봇 소개 + /try 체험 + 로그인. 경로는 같은 호스트의 admin 앱(basename /admin-ui).
const TRY = '/try' // 짧은 주소(#165) — 운영 nginx 가 /admin-ui/try 로 301 (쿼리 보존)
const INQUIRY = 'https://sandcorp.co.kr/#home-inquiry'

// 실제로 있는 기능만(CONTEXT.md Flow 절). 새 기능을 쓰려면 먼저 구현돼 있어야 한다.
const FEATURES = [
  { code: 'F-01', title: '흐름 편집기', desc: '상담을 노드와 선으로 그립니다. 지식 검색·AI 답변·조건·사람 넘김·가드레일을 이어 붙입니다.' },
  { code: 'F-02', title: '시험 대화', desc: '배포 전에 초안 그대로 대화해 봅니다. 사람 넘김 알림은 나가지 않습니다.' },
  { code: 'F-03', title: '판단 기록', desc: '한 턴마다 노드별 입력·출력·분기 이유·토큰·비용·걸린 시간을 남깁니다.' },
  { code: 'F-04', title: 'HTTP 도구', desc: '허용한 도메인의 HTTPS API를 흐름에서 부릅니다. 응답 값을 다음 노드가 씁니다.' },
  { code: 'F-05', title: '지식 검색', desc: '올린 문서를 지식그래프로 구조화해 근거를 찾아 답합니다.' },
  { code: 'F-06', title: '사람 넘김·가드레일', desc: '확신이 낮거나 방문자가 원하면 상담원에게 넘기고, 금지 표현은 막거나 고쳐 씁니다.' },
]

const TEMPLATES = [
  { key: 'home', title: '홈 상담', desc: '회사 질문은 지식 검색으로, 문의 방법은 문의 폼으로 안내합니다.' },
  { key: 'shop', title: '쇼핑몰 고객 지원', desc: '주문 조회·환불 규정 안내, 필요하면 사람에게 넘깁니다.' },
  { key: 'academy', title: '학원 상담', desc: '레벨 테스트 예약을 도구로 잡아 줍니다.' },
]

export default function Landing() {
  return (
    <div className="landing">
      <header className="site-head">
        <div className="container head-inner">
          <a href="/" className="brand"><FlowMark />Sandcorp Flow</a>
          <nav className="head-nav" aria-label="바로가기">
            <a href={TRY}>체험</a>
            <a href="#login">로그인</a>
            <ThemeToggle />
          </nav>
        </div>
      </header>

      <main>
        <section className="container hero">
          <div className="eyebrow">SANDCORP FLOW</div>
          <h1>흐름으로 만드는 상담 챗봇</h1>
          <p className="lead">
            상담의 길을 노드와 선으로 그리고, 시험 대화로 확인한 뒤 배포합니다.
            무엇을 근거로 어떻게 답했는지는 판단 기록으로 남습니다.
          </p>
          <div className="hero-cta">
            <a className="btn btn-primary" href={TRY}>체험해 보기</a>
            <a className="btn" href="#login">로그인</a>
          </div>
          <p className="hero-note">가입 없이 템플릿을 고쳐 보고 시험 대화까지 해 볼 수 있습니다.</p>
        </section>

        <section className="container section" role="region" aria-label="기능">
          <h2>기능</h2>
          <div className="grid">
            {FEATURES.map(f => (
              <div className="cell" key={f.code}>
                <span className="code">{f.code}</span>
                <h3>{f.title}</h3>
                <p>{f.desc}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="container section" role="region" aria-label="템플릿으로 바로 체험">
          <h2>템플릿으로 바로 체험</h2>
          <p className="sub">골라서 열면 그 흐름이 선택된 채로 시작합니다.</p>
          <ul className="rows">
            {TEMPLATES.map(t => (
              <li key={t.key}>
                <a className="row" href={`${TRY}?template=${t.key}`}>
                  <span className="row-title">{t.title}</span>
                  <span className="row-desc">{t.desc}</span>
                  <span className="row-go" aria-hidden="true">→</span>
                </a>
              </li>
            ))}
          </ul>
        </section>

        <section id="login" className="container section" role="region" aria-label="로그인">
          <h2>로그인</h2>
          <ul className="rows">
            <li>
              <a className="row" href="/admin-ui/">
                <span className="row-title">운영자 로그인</span>
                <span className="row-desc">테넌트와 사용량을 관리합니다.</span>
                <span className="row-go" aria-hidden="true">→</span>
              </a>
            </li>
            <li>
              <a className="row" href="/admin-ui/tenant">
                <span className="row-title">테넌트 로그인</span>
                <span className="row-desc">우리 회사의 흐름·문서·상담을 관리합니다.</span>
                <span className="row-go" aria-hidden="true">→</span>
              </a>
            </li>
          </ul>
        </section>

        <section className="container section inquiry">
          <a className="inquiry-link" href={INQUIRY}>도입이 궁금하면 문의 →</a>
        </section>
      </main>

      <SiteFooter />
    </div>
  )
}
