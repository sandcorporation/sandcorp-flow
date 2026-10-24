import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

// 링크 프로토콜 화이트리스트 — 위젯과 동일 정책(http/https/mailto + 스킴 없는 상대/앵커).
// javascript:·data: 등은 href를 비워 차단한다.
const SAFE_PROTOCOL = /^(https?:|mailto:):?/i

function safeUrl(url: string): string {
  if (!url) return ''
  const u = url.trim()
  if (SAFE_PROTOCOL.test(u)) return u
  if (/^[a-z][a-z0-9+.-]*:/i.test(u)) return ''   // 다른 스킴 → 제거
  return u                                          // 스킴 없음(상대/앵커) → 허용
}

const components: Components = {
  a: ({ href, children }) => (
    <a href={href || undefined} target="_blank" rel="noopener noreferrer nofollow"
       className="break-words text-foreground underline decoration-primary decoration-2 underline-offset-2">{children}</a>
  ),
  img: () => null,
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-1 list-disc pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1 list-decimal pl-5">{children}</ol>,
  li: ({ children }) => <li className="my-0.5">{children}</li>,
  h1: ({ children }) => <h1 className="mb-1 mt-2 text-base font-semibold">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-1 mt-2 text-[15px] font-semibold">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1 mt-2 text-sm font-semibold">{children}</h3>,
  h4: ({ children }) => <h4 className="mb-1 mt-2 text-sm font-semibold">{children}</h4>,
  h5: ({ children }) => <h5 className="mb-1 mt-2 text-sm font-semibold">{children}</h5>,
  h6: ({ children }) => <h6 className="mb-1 mt-2 text-sm font-semibold">{children}</h6>,
  code: ({ className, children }) => {
    const block = /language-/.test(className || '') || String(children).includes('\n')
    return block
      ? <code className="font-mono text-[0.85em]">{children}</code>
      : <code className="rounded-sm bg-foreground/10 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
  },
  pre: ({ children }) => (
    <pre className="my-1.5 overflow-x-auto rounded-md bg-foreground/10 p-2.5 font-mono">{children}</pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-1.5 border-l-2 border-muted-foreground/40 pl-2 text-muted-foreground">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="my-1.5 overflow-x-auto"><table className="border-collapse text-[0.9em]">{children}</table></div>
  ),
  th: ({ children }) => <th className="border border-border px-2 py-1 text-left">{children}</th>,
  td: ({ children }) => <td className="border border-border px-2 py-1">{children}</td>,
  hr: () => <hr className="my-2 border-border" />,
}

/**
 * admin 세션 콘솔의 AI(assistant) 메시지용 마크다운 렌더러.
 * 위젯 Markdown과 동일한 안전 정책(raw HTML off·이미지 off·링크 화이트리스트·GFM),
 * 스타일만 Tailwind 클래스로(themed·컴팩트 버블).
 */
export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={safeUrl} components={components}>
      {children || ''}
    </ReactMarkdown>
  )
}
