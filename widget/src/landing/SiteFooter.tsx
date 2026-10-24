import { BUSINESS } from './business'

// 정보통신망법·전자상거래법 사업자 정보 + 약관/처리방침 링크. 랜딩·약관·처리방침 페이지가 공유.
export default function SiteFooter() {
  return (
    <footer className="footer">
      <div className="container footer-inner">
        <div className="footer-top">
          <div className="footer-brand">
            <div className="footer-logo">Sandcorp Flow</div>
            <p className="footer-tagline">지식그래프 기반 AI 챗봇 플랫폼</p>
          </div>
          <nav className="footer-links">
            <a href="/">홈</a>
            <a href="/terms">이용약관</a>
            <a href="/privacy">개인정보처리방침</a>
          </nav>
        </div>
        <div className="footer-biz">
          <span><b>{BUSINESS.name}</b></span>
          <span>대표 {BUSINESS.ceo}</span>
          <span>사업자등록번호 {BUSINESS.bizNo}</span>
          {BUSINESS.salesNo && <span>통신판매업신고 {BUSINESS.salesNo}</span>}
          <span>주소 {BUSINESS.address}</span>
          <span>전화 {BUSINESS.tel}</span>
          <span>이메일 {BUSINESS.email}</span>
          <span>개인정보보호책임자 {BUSINESS.privacyOfficer}</span>
        </div>
        <div className="footer-copy">© {new Date().getFullYear()} {BUSINESS.name}. All rights reserved.</div>
      </div>
    </footer>
  )
}
