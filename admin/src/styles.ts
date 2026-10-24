// 인라인 스타일 묶음(OperatorLogin·MemoryTab). 색은 index.css 토큰(HMI 테마, ADR-0026)만 쓴다 — 하드코딩 색 금지.
// shadcn식 이름(card·primary 등)이든 HMI 이름(ink·sunken 등)이든 Flow 전용 --flow-* 토큰으로 푼다.
const FLOW: Record<string, string> = {
  card: 'surface', border: 'line', input: 'line', primary: 'accent', 'primary-foreground': 'accent-ink', secondary: 'raised',
  destructive: 'alarm', 'destructive-foreground': 'alarm-ink', 'muted-foreground': 'muted', foreground: 'text',
}
const c = (name: string, alpha?: number) => {
  const v = `var(--flow-${FLOW[name] ?? name})`
  return alpha === undefined ? `hsl(${v})` : `hsl(${v} / ${alpha})`
}

export const s: Record<string, any> = { // eslint-disable-line @typescript-eslint/no-explicit-any -- 함수형 항목(badge/tab)이 섞인 인라인 스타일 사전 (기존 모양 유지)
  center: { display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' },
  card: { background: c('card'), border: `1px solid ${c('border')}`, borderRadius: 2, padding: 32, width: 360, maxWidth: '100%' },
  title: { fontSize: 22, fontWeight: 700, marginBottom: 20, fontFamily: 'var(--flow-font-cond)', color: c('ink') },
  form: { display: 'flex', flexDirection: 'column', gap: 12 },
  input: { padding: '10px 12px', border: `1px solid ${c('input')}`, borderRadius: 2, fontSize: 14, outline: 'none', background: c('secondary') },
  btn: { padding: '10px 20px', background: c('primary'), color: c('primary-foreground'), border: `1px solid ${c('primary')}`, borderRadius: 2, cursor: 'pointer', fontWeight: 600, fontSize: 14 },
  btnSm: { padding: '6px 14px', background: c('primary'), color: c('primary-foreground'), border: `1px solid ${c('primary')}`, borderRadius: 2, cursor: 'pointer', fontWeight: 600, fontSize: 13 },
  btnDanger: { padding: '6px 14px', background: c('destructive'), color: c('destructive-foreground'), border: `1px solid ${c('destructive')}`, borderRadius: 2, cursor: 'pointer', fontWeight: 600, fontSize: 13 },
  error: { color: c('destructive'), fontSize: 13 },
  page: { maxWidth: 1100, margin: '0 auto', padding: 24 },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 },
  pageTitle: { fontSize: 24, fontWeight: 700, fontFamily: 'var(--flow-font-cond)', color: c('ink') },
  section: { background: c('card'), border: `1px solid ${c('border')}`, borderRadius: 2, padding: 20, marginBottom: 20 },
  sectionTitle: { fontSize: 16, fontWeight: 600, marginBottom: 16, fontFamily: 'var(--flow-font-cond)', letterSpacing: '0.04em', color: c('ink') },
  table: { width: '100%', borderCollapse: 'collapse' },
  th: { textAlign: 'left', padding: '10px 12px', borderBottom: `2px solid ${c('ink')}`, fontSize: 12, fontWeight: 600, fontFamily: 'var(--flow-font-cond)', letterSpacing: '0.06em', color: c('muted-foreground') },
  td: { padding: '10px 12px', borderBottom: `1px solid ${c('line-soft')}`, fontSize: 14 },
  badge: (active: boolean) => ({ padding: '1px 8px', borderRadius: 2, fontSize: 11, fontWeight: 500, fontFamily: 'var(--flow-font-mono)', border: `1px solid ${active ? c('ok') : c('alarm')}`, background: active ? c('ok', 0.15) : c('alarm', 0.15), color: active ? c('ok') : c('alarm') }),
  alert: { background: c('sunken'), border: `1px solid ${c('line-soft')}`, borderLeft: `3px solid ${c('primary')}`, borderRadius: 2, padding: '12px 16px', marginBottom: 20, fontSize: 14 },
  code: { background: c('sunken'), border: `1px solid ${c('line-soft')}`, padding: '1px 6px', borderRadius: 2, fontFamily: 'var(--flow-font-mono)', fontSize: 12 },
  tabs: { display: 'flex', gap: 0, marginBottom: 20, background: c('sunken'), border: `1px solid ${c('border')}`, borderRadius: 2 },
  tab: (active: boolean) => ({ padding: '8px 20px', border: 'none', borderRight: `1px solid ${c('line-soft')}`, borderRadius: 0, cursor: 'pointer', fontWeight: active ? 600 : 400, fontFamily: 'var(--flow-font-cond)', letterSpacing: '0.04em', background: active ? c('card') : 'transparent', boxShadow: active ? `inset 0 3px 0 ${c('primary')}` : 'none', color: active ? c('ink') : c('muted-foreground'), fontSize: 14 }),
  tabContent: { background: c('card'), border: `1px solid ${c('border')}`, borderRadius: 2, padding: 20 },
  label: { display: 'block', fontWeight: 600, fontSize: 12, marginBottom: 6, fontFamily: 'var(--flow-font-cond)', letterSpacing: '0.06em', color: c('muted-foreground') },
}
