import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import EmbedGuideCard, { buildIframeCode } from './EmbedGuideCard'

const ORIGIN = 'https://flow.example.com'

describe('buildIframeCode', () => {
  it('운영 표(docs/deployment.md)와 같은 iframe 문자열을 만든다', () => {
    expect(buildIframeCode(ORIGIN, 'sandcorp')).toBe(
      '<iframe src="https://flow.example.com/chatbot/sandcorp/" width="400" height="600" style="border:none" allow="microphone"></iframe>',
    )
  })
})

describe('EmbedGuideCard', () => {
  let writeText: ReturnType<typeof vi.fn>
  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
  })

  it('slug가 있으면 코드를 보여 주고 복사 버튼이 그 코드를 clipboard에 쓴다', async () => {
    render(<EmbedGuideCard origin={ORIGIN} slug="sandcorp" isAdmin />)
    const code = buildIframeCode(ORIGIN, 'sandcorp')
    expect(screen.getByText('내 사이트에 붙이기')).toBeInTheDocument()
    expect(screen.getByTestId('embed-code')).toHaveTextContent(code)
    await userEvent.click(screen.getByRole('button', { name: /임베드 코드 복사/ }))
    expect(writeText).toHaveBeenCalledWith(code)
    await userEvent.click(screen.getByRole('button', { name: /URL 복사/ }))
    expect(writeText).toHaveBeenCalledWith('https://flow.example.com/chatbot/sandcorp/')
  })

  it('미리보기 링크는 공개 챗봇 주소를 새 탭으로 연다', () => {
    render(<EmbedGuideCard origin={ORIGIN} slug="sandcorp" isAdmin />)
    const link = screen.getByRole('link', { name: /미리보기/ })
    expect(link).toHaveAttribute('href', 'https://flow.example.com/chatbot/sandcorp/')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('slug가 없으면 코드·복사 대신 slug 정하기 안내를 보인다', () => {
    render(<EmbedGuideCard origin={ORIGIN} slug="" isAdmin />)
    expect(screen.getByText(/slug를 먼저 정하고 저장/)).toBeInTheDocument()
    expect(screen.queryByTestId('embed-code')).toBeNull()
    expect(screen.queryByRole('button', { name: /복사/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /미리보기/ })).toBeNull()
  })

  it('Member는 slug가 없으면 관리자에게 요청하라고 안내한다', () => {
    render(<EmbedGuideCard origin={ORIGIN} slug="" isAdmin={false} />)
    expect(screen.getByText(/관리자가 slug를 정하면/)).toBeInTheDocument()
  })

  it('비밀키를 화면에 내지 않고 서버 서명 안내만 한다', () => {
    render(<EmbedGuideCard origin={ORIGIN} slug="sandcorp" isAdmin />)
    expect(screen.getByText(/서버에서/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/TENANT_KEY\s*=|sk-/)
  })
})
