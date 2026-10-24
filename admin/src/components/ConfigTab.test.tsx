import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import ConfigTab from './ConfigTab'
import * as api from '../api'

vi.mock('../api')

const baseConfig = {
  model_id: 'm', system_prompt: 's', agent_display_name: 'a',
  webhook_url: '', webhook_type: '', welcome_message: '',
  brand_name: '', hitl_enabled: true, require_identity_verification: false,
  llm_provider_type: '', llm_base_url: '', llm_api_key: '', extraction_model: '',
  embed_provider_type: '', embed_base_url: '', embed_api_key: '', embed_model: '', embed_dim: 1024,
  ocr_provider_type: '', ocr_base_url: '', ocr_api_key: '', ocr_model: '',
  platform_default_providers_enabled: true,
}

function mockConfig(overrides = {}) {
  vi.mocked(api.getTenantConfig).mockResolvedValue({ ...baseConfig, ...overrides } as any)
  vi.mocked(api.updateTenantConfig).mockResolvedValue({} as any)
}

// ConfigTab은 ?section= 세부 탭을 useSearchParams로 읽으므로 라우터 래핑이 필요하다.
// 각 테스트는 대상 필드가 있는 세부 탭에서 렌더한다(general | ai | handoff | security).
function renderConfig(section = 'general') {
  return render(
    <MemoryRouter initialEntries={[`/tenant/config?section=${section}`]}>
      <ConfigTab />
    </MemoryRouter>,
  )
}

async function save() {
  await userEvent.click(screen.getByRole('button', { name: '저장' }))
}

beforeEach(() => {
  vi.clearAllMocks()
  mockConfig()
  vi.mocked(api.currentAgentRole).mockReturnValue('admin')  // 기본은 Admin(전 컨트롤 노출)
})

describe('ConfigTab — 세부 탭', () => {
  it('기본 탭은 일반이고, AI 모델 탭으로 전환하면 LLM Provider 타입이 보인다', async () => {
    renderConfig('general')
    await screen.findByLabelText('브랜드 텍스트')
    expect(screen.queryByLabelText('LLM Provider 타입')).toBeNull()  // 다른 탭이라 미노출
    await userEvent.click(screen.getByRole('tab', { name: 'AI 모델' }))
    expect(await screen.findByLabelText('LLM Provider 타입')).toBeInTheDocument()
    expect(screen.queryByLabelText('브랜드 텍스트')).toBeNull()
  })

  it('?section=ai 딥링크로 진입하면 AI 모델 탭이 렌더된다', async () => {
    renderConfig('ai')
    expect(await screen.findByLabelText('LLM Provider 타입')).toBeInTheDocument()
  })

  it('탭을 오가도 입력이 보존되어 한 번에 atomic 저장된다', async () => {
    renderConfig('general')
    await userEvent.type(await screen.findByLabelText('브랜드 텍스트'), 'ATOM샵')
    await userEvent.click(screen.getByRole('tab', { name: '상담 전환' }))
    await userEvent.click(await screen.findByLabelText('HITL 사용'))  // 끄기
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({ brand_name: 'ATOM샵', hitl_enabled: false }),
    ))
  })
})

describe('ConfigTab — 친절 설명', () => {
  it('AI 탭에 API Key·Embedding 평이한 설명이 보인다', async () => {
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')
    expect(screen.getByText(/AI 서비스에서 발급받은 비밀 키/)).toBeInTheDocument()
    expect(screen.getByText(/검색하도록.*바꾸는 엔진/)).toBeInTheDocument()
  })
})

describe('ConfigTab — HITL 토글', () => {
  it('hitl_enabled 토글을 끄고 저장하면 hitl_enabled=false를 보낸다', async () => {
    renderConfig('handoff')
    const toggle = await screen.findByLabelText('HITL 사용')
    expect(toggle).toBeChecked()
    await userEvent.click(toggle)
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({ hitl_enabled: false }),
    ))
  })
})

describe('ConfigTab — 영업시간(상담 가능 시간)', () => {
  it('handoff 탭에서 타임존·요일별 시간을 편집하면 hitl_timezone/hitl_schedule을 저장 payload에 담는다', async () => {
    renderConfig('handoff')
    await screen.findByLabelText('HITL 사용')

    await userEvent.type(screen.getByLabelText('타임존'), 'Asia/Seoul')
    await userEvent.click(screen.getByLabelText('월 영업'))            // 월요일 켜기 → 시간 입력 활성화
    fireEvent.change(screen.getByLabelText('월 시작'), { target: { value: '09:00' } })
    fireEvent.change(screen.getByLabelText('월 종료'), { target: { value: '18:00' } })
    await save()

    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        hitl_timezone: 'Asia/Seoul',
        hitl_schedule: expect.objectContaining({
          mon: expect.objectContaining({ enabled: true, start: '09:00', end: '18:00' }),
        }),
      }),
    ))
  })

  it('휴일을 추가하면 hitl_holidays에 담기고, HITL을 끄면 영업시간 편집기가 사라진다', async () => {
    renderConfig('handoff')
    const toggle = await screen.findByLabelText('HITL 사용')

    fireEvent.change(screen.getByLabelText('휴일 추가'), { target: { value: '2026-01-01' } })
    await userEvent.click(screen.getByRole('button', { name: '추가' }))
    expect(screen.getByText('2026-01-01')).toBeInTheDocument()

    await userEvent.click(toggle)  // HITL 끄기 → 영업시간 섹션 비노출
    expect(screen.queryByLabelText('타임존')).toBeNull()

    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({ hitl_holidays: ['2026-01-01'] }),
    ))
  })
})

describe('ConfigTab — 브랜드/신원검증', () => {
  it('brand_name 입력을 저장 payload에 담는다', async () => {
    renderConfig('general')
    await userEvent.type(await screen.findByLabelText('브랜드 텍스트'), 'ABC샵')
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({ brand_name: 'ABC샵' }),
    ))
  })

  it('신원검증 토글을 켜고 저장하면 require_identity_verification=true', async () => {
    renderConfig('security')
    const toggle = await screen.findByLabelText('visitor_id 신원검증 요구')
    expect(toggle).not.toBeChecked()
    await userEvent.click(toggle)
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({ require_identity_verification: true }),
    ))
  })
})

describe('ConfigTab — LLM Provider', () => {
  it('LLM provider 설정을 저장 payload에 담는다', async () => {
    renderConfig('ai')
    await userEvent.selectOptions(await screen.findByLabelText('LLM Provider 타입'), 'custom')
    await userEvent.type(screen.getByLabelText('LLM Base URL'), 'https://x/v1')
    await userEvent.type(screen.getByLabelText('LLM API Key'), 'sk-llm')
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(expect.objectContaining({
      llm_provider_type: 'custom', llm_base_url: 'https://x/v1', llm_api_key: 'sk-llm',
    })))
  })
})

describe('ConfigTab — AI 모델', () => {
  it('불러온 모델 중 하나를 골라 model_id로 저장한다', async () => {
    // 모델 옵션은 provider에서 조회한 것만(하드코딩 제안 없음) → 불러온 뒤 선택한다.
    vi.mocked(api.fetchProviderModels).mockResolvedValue(['gpt-4o', 'gpt-4o-mini'])
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')   // config 로딩 완료 대기
    await userEvent.click(screen.getByRole('button', { name: 'LLM 모델 불러오기' }))
    await screen.findByRole('option', { name: 'gpt-4o' })
    await userEvent.selectOptions(screen.getByLabelText('AI 모델'), 'gpt-4o')
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({ model_id: 'gpt-4o' }),
    ))
  })
})

describe('ConfigTab — 고급 설정(자료 정리 모델)', () => {
  it('기본적으로 자료 정리 모델은 접혀 있다(비개발자 단순 뷰)', async () => {
    renderConfig('ai')
    await screen.findByLabelText('AI 모델')
    expect(screen.queryByLabelText('자료 정리 모델')).toBeNull()
  })

  it('고급 설정을 펼쳐 자료 정리 모델을 지정하면 extraction_model로 저장한다', async () => {
    renderConfig('ai')
    await screen.findByLabelText('AI 모델')
    await userEvent.click(screen.getByRole('button', { name: /자료 정리 모델/ }))
    await userEvent.type(screen.getByLabelText('자료 정리 모델 직접 입력'), 'extract-model')
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({ extraction_model: 'extract-model' }),
    ))
  })
})

describe('ConfigTab — OCR(Vision) Provider', () => {
  it('OCR provider 설정을 저장 payload에 담는다', async () => {
    renderConfig('ai')
    await userEvent.selectOptions(await screen.findByLabelText('OCR Provider 타입'), 'custom')
    await userEvent.type(screen.getByLabelText('OCR Base URL'), 'https://x/v1')
    await userEvent.type(screen.getByLabelText('OCR API Key'), 'sk-ocr')
    await userEvent.type(screen.getByLabelText('OCR 모델 직접 입력'), 'gpt-4o')
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(expect.objectContaining({
      ocr_provider_type: 'custom', ocr_base_url: 'https://x/v1',
      ocr_api_key: 'sk-ocr', ocr_model: 'gpt-4o',
    })))
  })

  it('OCR Provider 타입에 anthropic 옵션이 있다(vision)', async () => {
    renderConfig('ai')
    const select = await screen.findByLabelText('OCR Provider 타입')
    expect(select.querySelector('option[value="anthropic"]')).not.toBeNull()
  })
})

describe('ConfigTab — Embedding Provider', () => {
  it('embedding provider 설정(차원 포함)을 저장 payload에 담는다', async () => {
    renderConfig('ai')
    await userEvent.selectOptions(await screen.findByLabelText('Embedding Provider 타입'), 'custom')
    await userEvent.type(screen.getByLabelText('Embedding Base URL'), 'https://api.openai.com/v1')
    await userEvent.type(screen.getByLabelText('Embedding API Key'), 'sk-emb')
    await userEvent.type(screen.getByLabelText('Embedding 모델 직접 입력'), 'text-embedding-3-small')
    const dim = screen.getByLabelText('Embedding 차원')
    await userEvent.clear(dim)
    await userEvent.type(dim, '1536')
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(expect.objectContaining({
      embed_provider_type: 'custom', embed_base_url: 'https://api.openai.com/v1',
      embed_api_key: 'sk-emb', embed_model: 'text-embedding-3-small', embed_dim: 1536,
    })))
  })
})

describe('ConfigTab — Base URL은 custom일 때만 노출', () => {
  it('기본/openai 타입에선 Base URL 입력이 숨겨진다', async () => {
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')
    expect(screen.queryByLabelText('LLM Base URL')).toBeNull()
    expect(screen.queryByLabelText('Embedding Base URL')).toBeNull()
    await userEvent.selectOptions(screen.getByLabelText('LLM Provider 타입'), 'openai')
    expect(screen.queryByLabelText('LLM Base URL')).toBeNull()
  })

  it('custom 타입을 고르면 Base URL 입력이 나타난다', async () => {
    renderConfig('ai')
    await userEvent.selectOptions(await screen.findByLabelText('LLM Provider 타입'), 'custom')
    expect(screen.getByLabelText('LLM Base URL')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Embedding Provider 타입'), 'custom')
    expect(screen.getByLabelText('Embedding Base URL')).toBeInTheDocument()
  })

  it('custom LLM Provider 도움말에 스트리밍 사용량 옵션(include_usage)을 안내한다', async () => {
    renderConfig('ai')
    expect(screen.queryByText(/stream_options\(include_usage\)/)).toBeNull()
    await userEvent.selectOptions(await screen.findByLabelText('LLM Provider 타입'), 'custom')
    expect(screen.getByText(/스트리밍 사용량 집계를 위해 stream_options\(include_usage\)를 보냅니다.*오류가 날 수 있습니다/)).toBeInTheDocument()
  })

  it('custom→openai로 바꾸면 base_url을 비워 저장한다(표준 주소 사용)', async () => {
    mockConfig({ embed_provider_type: 'custom', embed_base_url: 'https://old-custom/v1' })
    renderConfig('ai')
    await userEvent.selectOptions(await screen.findByLabelText('Embedding Provider 타입'), 'openai')
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(
      expect.objectContaining({ embed_provider_type: 'openai', embed_base_url: '' }),
    ))
  })
})

describe('ConfigTab — 플랫폼 기본 Provider 게이팅', () => {
  it('platform_default_providers_enabled=false면 "기본" Provider 옵션을 숨긴다', async () => {
    mockConfig({ platform_default_providers_enabled: false })
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')
    expect(screen.queryByRole('option', { name: /기본 \(dev: OpenRouter\)/})).toBeNull()
    expect(screen.queryByRole('option', { name: /기본.*ollama/ })).toBeNull()
  })

  it('platform_default_providers_enabled=true면 "기본" 옵션을 보여준다', async () => {
    mockConfig({ platform_default_providers_enabled: true })
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')
    expect(screen.queryByRole('option', { name: /기본 \(dev: OpenRouter\)/})).not.toBeNull()
  })

  it('LLM Provider 안내 문구도 게이팅된다 — prod면 OpenRouter 폴백 문구 대신 "설정 필수"를 보인다', async () => {
    mockConfig({ platform_default_providers_enabled: false })
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')
    expect(screen.queryByText(/플랫폼 기본\(OpenRouter\)을 씁니다/)).toBeNull()
    expect(screen.getByText(/체험 한도\(대화 턴\) 안에서 플랫폼 키로 답합니다/)).toBeInTheDocument()
  })

  it('dev(platform_default_providers_enabled=true)면 OpenRouter 폴백 문구를 보인다', async () => {
    mockConfig({ platform_default_providers_enabled: true })
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')
    expect(screen.getByText(/플랫폼 기본\(OpenRouter\)을 씁니다/)).toBeInTheDocument()
  })

  it('OCR은 플랫폼 기본이 없다 — dev에서도 "(미설정)"과 업로드 차단 안내를 보인다(ADR-0020 보충)', async () => {
    mockConfig({ platform_default_providers_enabled: true })
    renderConfig('ai')
    await screen.findByLabelText('OCR Provider 타입')
    expect(screen.queryByRole('option', { name: /PaddleOCR/ })).toBeNull()
    expect(screen.queryByText(/PaddleOCR/)).toBeNull()
    expect(screen.getByText(/미설정 시 이미지·스캔 문서 업로드는 막힙니다/)).toBeInTheDocument()
  })
})

describe('ConfigTab — 모델 불러오기', () => {
  it('LLM 모델 불러오기가 model_id 옵션을 채운다', async () => {
    vi.mocked(api.fetchProviderModels).mockResolvedValue(['gpt-4o', 'gpt-4o-mini'])
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')
    await userEvent.click(screen.getByRole('button', { name: 'LLM 모델 불러오기' }))
    await waitFor(() => expect(screen.getAllByRole('option', { name: 'gpt-4o-mini' }).length).toBeGreaterThan(0))
    expect(vi.mocked(api.fetchProviderModels).mock.calls[0][0]).toBe('llm')
  })

  it('Embedding 모델 불러오기가 embed_model 옵션을 채운다', async () => {
    vi.mocked(api.fetchProviderModels).mockResolvedValue(['text-embedding-3-small'])
    renderConfig('ai')
    await screen.findByLabelText('Embedding Provider 타입')
    await userEvent.click(screen.getByRole('button', { name: 'Embedding 모델 불러오기' }))
    await waitFor(() => expect(screen.getByRole('option', { name: 'text-embedding-3-small' })).toBeInTheDocument())
    expect(vi.mocked(api.fetchProviderModels).mock.calls[0][0]).toBe('embed')
  })

  it('LLM 조회 실패 시 LLM 섹션에 에러를 표시한다(임베딩 에러와 별개)', async () => {
    vi.mocked(api.fetchProviderModels).mockRejectedValue(new Error('연결 실패'))
    renderConfig('ai')
    await screen.findByLabelText('LLM Provider 타입')
    await userEvent.click(screen.getByRole('button', { name: 'LLM 모델 불러오기' }))
    await waitFor(() => expect(screen.getByText(/LLM 모델 조회 실패/)).toBeInTheDocument())
    expect(screen.queryByText(/Embedding 모델 조회 실패/)).toBeNull()
  })

  it('Embedding 조회 실패 시 Embedding 섹션에 에러를 표시한다(LLM 에러와 별개)', async () => {
    vi.mocked(api.fetchProviderModels).mockRejectedValue(new Error('연결 실패'))
    renderConfig('ai')
    await screen.findByLabelText('Embedding Provider 타입')
    await userEvent.click(screen.getByRole('button', { name: 'Embedding 모델 불러오기' }))
    await waitFor(() => expect(screen.getByText(/Embedding 모델 조회 실패/)).toBeInTheDocument())
    expect(screen.queryByText(/LLM 모델 조회 실패/)).toBeNull()
  })
})

describe('ConfigTab — OpenAI 한방 설정', () => {
  it('미설정(LLM 빈값)이면 한방 카드가 보이고, 키 입력→시작하기가 quickSetupOpenAI를 호출한다', async () => {
    vi.mocked(api.quickSetupOpenAI).mockResolvedValue({
      ...baseConfig, llm_provider_type: 'openai', model_id: 'gpt-4o-mini',
      embed_provider_type: 'openai', ocr_provider_type: 'openai',
    } as any)
    renderConfig('ai')
    await userEvent.type(await screen.findByLabelText('OpenAI API Key'), 'sk-abc')
    await userEvent.click(screen.getByRole('button', { name: '시작하기' }))
    await waitFor(() => expect(api.quickSetupOpenAI).toHaveBeenCalledWith('sk-abc'))
  })

  it('3종이 같은 provider면 컴팩트 요약 카드를 보이고 한방 카드는 숨긴다', async () => {
    mockConfig({
      llm_provider_type: 'openai', model_id: 'gpt-4o-mini',
      embed_provider_type: 'openai', embed_model: 'text-embedding-3-small',
      ocr_provider_type: 'openai', ocr_model: 'gpt-4o-mini',
    })
    renderConfig('ai')
    expect(await screen.findByText(/AI 제공자: OpenAI/)).toBeInTheDocument()
    expect(screen.queryByLabelText('OpenAI API Key')).toBeNull()
  })

  it('provider가 섞이면 요약·한방 없이 상세 Provider 설정이 보인다', async () => {
    mockConfig({ llm_provider_type: 'openai', embed_provider_type: 'custom', ocr_provider_type: 'anthropic' })
    renderConfig('ai')
    expect(await screen.findByLabelText('LLM Provider 타입')).toBeInTheDocument()
    expect(screen.queryByText(/AI 제공자:/)).toBeNull()
    expect(screen.queryByLabelText('OpenAI API Key')).toBeNull()
  })
})

describe('ConfigTab — Tenant Slug', () => {
  it('slug 입력 후 Slug 저장을 누르면 updateTenantSlug를 호출한다', async () => {
    vi.mocked(api.updateTenantSlug).mockResolvedValue({ slug: 'abc-shop' } as any)
    renderConfig('security')
    await userEvent.type(await screen.findByLabelText('Tenant Slug'), 'abc-shop')
    await userEvent.click(screen.getByRole('button', { name: /Slug 저장/ }))
    await waitFor(() => expect(api.updateTenantSlug).toHaveBeenCalledWith('abc-shop'))
  })
})

describe('ConfigTab — 공개 URL(slug) 한글 안내 (issue 189)', () => {
  it('slug 입력 힌트에 한글 가능 안내가 보인다', async () => {
    renderConfig('security')
    expect(await screen.findByText(/한글·영문·숫자·하이픈을 쓸 수 있어요/)).toBeInTheDocument()
  })
})

describe('ConfigTab — 공개 URL(slug) 복원·복사', () => {
  it('config의 slug가 입력 필드에 복원된다(새로고침)', async () => {
    mockConfig({ slug: '우리가게' })
    renderConfig('security')
    expect(await screen.findByLabelText('Tenant Slug')).toHaveValue('우리가게')
  })

  it('URL·임베드 코드 복사 버튼이 clipboard에 쓴다', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    mockConfig({ slug: 'myshop' })
    renderConfig('security')
    await screen.findByLabelText('Tenant Slug')

    await userEvent.click(screen.getByRole('button', { name: /URL 복사/ }))
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('/chatbot/myshop/'))

    await userEvent.click(screen.getByRole('button', { name: /임베드 코드 복사/ }))
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('<iframe'))
  })

  it('slug가 없으면 복사 버튼이 보이지 않는다', async () => {
    mockConfig({ slug: '' })
    renderConfig('security')
    await screen.findByLabelText('Tenant Slug')
    expect(screen.queryByRole('button', { name: /URL 복사/ })).toBeNull()
  })
})

describe('ConfigTab — 주제범위 제어', () => {
  it('토글을 켜면 응대 범위·거절 문구 입력이 나타난다', async () => {
    renderConfig('general')
    const toggle = await screen.findByLabelText('주제범위 제어')
    expect(screen.queryByLabelText('응대 범위')).toBeNull()
    await userEvent.click(toggle)
    expect(screen.getByLabelText('응대 범위')).toBeInTheDocument()
    expect(screen.getByLabelText('거절 문구')).toBeInTheDocument()
  })

  it('토글+범위+거절문구를 저장 payload에 담는다', async () => {
    renderConfig('general')
    await userEvent.click(await screen.findByLabelText('주제범위 제어'))
    await userEvent.type(screen.getByLabelText('응대 범위'), '주문·배송 문의')
    await userEvent.type(screen.getByLabelText('거절 문구'), '쇼핑만 도와드려요')
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalledWith(expect.objectContaining({
      topic_scope_enabled: true, scope_description: '주문·배송 문의', scope_refusal_message: '쇼핑만 도와드려요',
    })))
  })

  it('범위 없이 켜고 저장하면 막고 안내하며 저장을 호출하지 않는다', async () => {
    renderConfig('general')
    await userEvent.click(await screen.findByLabelText('주제범위 제어'))
    await save()
    expect(screen.getByText(/응대 범위를 입력하세요/)).toBeInTheDocument()
    expect(api.updateTenantConfig).not.toHaveBeenCalled()
  })
})

describe('ConfigTab — 역할 게이팅 (issue 210)', () => {
  it('Member는 Slug 변경·API KEY 재발급 컨트롤을 보지 못한다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    renderConfig('security')
    expect(await screen.findByText('Slug 변경은 Admin만 할 수 있습니다.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Slug 저장/ })).toBeNull()
    expect(screen.queryByText('API KEY 재발급')).toBeNull()
  })

  it('요금 탭은 「준비 중」과 이 조직의 체험 상태를 보여 주고 저장 버튼은 없다 (#41)', async () => {
    vi.mocked(api.getTrial).mockResolvedValue({ state: 'trial', remaining: 12, limit: 200 })
    renderConfig()
    await userEvent.click(await screen.findByRole('tab', { name: '요금' }))
    expect(screen.getByText('요금제는 준비 중입니다.')).toBeInTheDocument()
    expect(await screen.findByTestId('pricing-trial-status')).toHaveTextContent('남은 대화 12/200턴')
    expect(screen.queryByRole('button', { name: '저장' })).toBeNull()
  })
})

describe('ConfigTab — 배포 승인 옵션', () => {
  it('Admin 이 「배포에 관리자 승인 필요」를 켜고 저장하면 설정에 실린다', async () => {
    renderConfig('deploy')
    const box = await screen.findByLabelText('배포에 관리자 승인 필요')
    expect(box).not.toBeChecked()
    await userEvent.click(box)
    await save()
    await waitFor(() => expect(api.updateTenantConfig).toHaveBeenCalled())
    expect(vi.mocked(api.updateTenantConfig).mock.calls[0][0]).toMatchObject({ deploy_requires_approval: true })
  })

  it('켜진 상태로 열면 체크돼 있다', async () => {
    mockConfig({ deploy_requires_approval: true })
    renderConfig('deploy')
    expect(await screen.findByLabelText('배포에 관리자 승인 필요')).toBeChecked()
  })

  it('Admin 이 아니면 끄고 이유를 보인다', async () => {
    vi.mocked(api.currentAgentRole).mockReturnValue('member')
    renderConfig('deploy')
    expect(await screen.findByLabelText('배포에 관리자 승인 필요')).toBeDisabled()
    expect(screen.getByText(/관리자만 바꿀 수 있습니다/)).toBeInTheDocument()
  })
})
