import SiteFooter from './SiteFooter'
import FlowMark from './FlowMark'
import ThemeToggle from './ThemeToggle'
import { BUSINESS } from './business'

// 개인정보처리방침 — 표준 템플릿(개인정보보호법 기준)에 플랫폼 특성을 반영. 시행 전 법률 검토 권장.
export default function Privacy() {
  return (
    <div className="landing">
      <header className="doc-header">
        <div className="container doc-head-inner"><a href="/" className="doc-home">← <FlowMark />Sandcorp Flow</a><ThemeToggle /></div>
      </header>
      <main className="container doc">
        <h1>개인정보처리방침</h1>
        <p className="doc-meta">시행일: 2026. 6. 24. (최근 개정: 2026. 11. 7.)</p>
        <p className="doc-note">본 방침은 표준 템플릿이며, 실제 시행 전 법률 전문가의 검토를 권장합니다.</p>

        <p>{BUSINESS.name}(이하 ‘회사’)은 「개인정보 보호법」 등 관련 법령을 준수하며, 정보주체의
        개인정보를 보호하기 위해 다음과 같은 처리방침을 둡니다.</p>

        <h2>1. 수집하는 개인정보 항목 및 방법</h2>
        <ul>
          <li>운영자·상담원 계정: 이메일, 사용자명, 비밀번호(암호화 저장)</li>
          <li>방문자(Visitor): 채팅 메시지 내용, 익명 방문자 식별자(브라우저 localStorage), 대화에서 추출된 메모리</li>
          <li>고객사(Tenant)가 업로드한 문서·웹 콘텐츠</li>
          <li>서비스 오류 기록(자동 수집): 오류 내용·발생 위치, 발생 시각, 서비스 버전, 페이지 주소(URL), 브라우저·기기 정보(User-Agent), 서버 오류의 요청 경로·방식, 접속 IP 주소(끝자리를 가려 저장 — IPv4는 마지막 8비트, IPv6는 /64 이후를 0으로 바꿔 정확한 주소는 남지 않습니다).
            쿠키·인증 헤더·로그인 토큰·비밀번호·요청 본문·지역 변수는 보내기 전에 지우며, 이메일·이름은 일부러 담지 않습니다.
            오류를 발견·수정하고 서비스를 안정적으로 운영하기 위해서만 쓰며, 90일 뒤 자동으로 삭제합니다.
            회사가 직접 운영하는 국내 서버로만 보내며 위탁·제3자 제공·국외 이전 없음.
            자세한 내용은 공통 문서 <a href="https://sandcorporation.github.io/privacy-and-terms/errors/" target="_blank" rel="noopener noreferrer">Sandcorp 서비스 오류 기록</a>을 참고하세요.</li>
          <li>수집 방법: 회원가입·서비스 이용·챗봇 대화 과정에서 자동 또는 입력으로 수집</li>
        </ul>

        <h2>2. 개인정보의 이용 목적</h2>
        <p>챗봇 응답 제공, 지식그래프 구축·검색, 상담원 연결(HITL), 서비스 운영·개선, 문의 응대.</p>

        <h2>3. 보유 및 이용 기간</h2>
        <p>회원 탈퇴 또는 계약 종료 시 지체 없이 파기합니다. 다만 관계 법령에서 정한 기간 동안은 보관합니다.</p>

        <h2>4. 개인정보의 제3자 제공</h2>
        <p>회사는 정보주체의 동의 없이 개인정보를 제3자에게 제공하지 않습니다. 단, 법령에 특별한 규정이 있는 경우는 예외로 합니다.</p>

        <h2>5. 개인정보 처리의 위탁</h2>
        <p>서비스 제공을 위해 다음 업무를 위탁할 수 있습니다 — 클라우드 인프라(호스팅), 고객사가 설정한 LLM·임베딩 Provider(예: OpenAI 등) 호출. 서비스 오류 기록은 회사가 직접 운영하는 서버에만 모이므로 위탁에 해당하지 않습니다. 위탁 시 관련 법령에 따라 안전하게 관리합니다.</p>

        <h2>6. 정보주체의 권리·의무 및 행사 방법</h2>
        <p>정보주체는 언제든지 개인정보 열람·정정·삭제·처리정지를 요구할 수 있으며, 아래 보호책임자에게 연락하여 행사할 수 있습니다.</p>

        <h2>7. 개인정보의 파기</h2>
        <p>처리 목적이 달성되면 지체 없이 파기합니다. 전자적 파일은 복구 불가능한 방법으로 삭제합니다.</p>

        <h2>8. 개인정보 보호책임자</h2>
        <p>성명: {BUSINESS.privacyOfficer} · 이메일: {BUSINESS.email} · 전화: {BUSINESS.tel}</p>

        <h2>9. 처리방침의 변경</h2>
        <p>본 방침은 법령·서비스 변경에 따라 개정될 수 있으며, 변경 시 본 페이지를 통해 공지합니다.</p>
        <p>개정 이력: 2026. 11. 7. — 서비스 오류 기록 항목 추가</p>
        <p>개정 이력: 2026. 11. 7. — 오류 기록에 가린 접속 IP 추가</p>
      </main>
      <SiteFooter />
    </div>
  )
}
