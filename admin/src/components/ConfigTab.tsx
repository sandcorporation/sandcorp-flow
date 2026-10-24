import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getTenantConfig, updateTenantConfig, resetTenantKey, updateTenantSlug, fetchProviderModels, quickSetupOpenAI, currentAgentRole } from '../api'
import type { TenantConfigOut } from '../generated/model'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import PricingSection from './PricingSection'

type DaySchedule = { enabled?: boolean; start?: string; end?: string }
const WEEKDAYS: [string, string][] = [
  ['mon', '월'], ['tue', '화'], ['wed', '수'], ['thu', '목'], ['fri', '금'], ['sat', '토'], ['sun', '일'],
]

// 모델 드롭다운 후보 = provider에서 실제 조회한 모델 + 현재 저장값(중복 제거). 하드코딩 제안은
// 두지 않는다 — provider-agnostic하게 섞이면(예: OpenAI인데 anthropic/* 노출) 혼란·오작동한다.
// 목록에 없는 모델은 바로 아래 "직접 입력"으로 넣는다.
function modelOptions(loaded: string[], current: string): string[] {
  return Array.from(new Set([...loaded, current].filter(Boolean)))
}

const WEBHOOK_TYPES = [
  { value: '', label: '없음' }, { value: 'slack', label: 'Slack' },
  { value: 'discord', label: 'Discord' }, { value: 'generic', label: 'Generic' },
]
const SECTIONS = [
  { id: 'general', label: '일반', intro: '봇이 손님에게 어떻게 보이고 말하는지 정합니다.' },
  { id: 'ai', label: 'AI 모델', intro: '챗봇을 움직이는 AI 엔진을 고릅니다. 가장 기술적인 부분이라, 모르면 담당 개발자와 함께 설정하세요.' },
  { id: 'handoff', label: '상담 전환', intro: 'AI가 답하기 어려울 때 사람 상담원에게 넘기는 방식을 정합니다.' },
  { id: 'security', label: '공개 URL·보안', intro: '챗봇 공개 주소와 접근 보안을 관리합니다.' },
  { id: 'billing', label: '요금', intro: '요금제와 이 조직의 체험 상태를 봅니다.' },
]

const hint = 'mt-1 text-xs text-muted-foreground'
const errorCls = 'mt-1 text-sm text-destructive'

export default function ConfigTab() {
  const [searchParams, setSearchParams] = useSearchParams()
  const section = searchParams.get('section') || 'general'
  const setSection = (id: string) => setSearchParams({ section: id }, { replace: true })

  const [config, setConfig] = useState<TenantConfigOut>({
    model_id: '', system_prompt: '', agent_display_name: '상담원',
    webhook_url: '', webhook_type: '', welcome_message: '',
  } as unknown as TenantConfigOut)
  const isAdmin = currentAgentRole() === 'admin'  // Slug 변경·키 재발급은 Admin 전용(ADR-0025)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [newKey, setNewKey] = useState<string | null>(null)
  const [resetConfirm, setResetConfirm] = useState(false)
  const [slug, setSlug] = useState('')
  const [slugSaved, setSlugSaved] = useState(false)
  const [copied, setCopied] = useState<'' | 'url' | 'iframe'>('')
  const [llmModels, setLlmModels] = useState<string[]>([])
  const [embedModels, setEmbedModels] = useState<string[]>([])
  const [ocrModels, setOcrModels] = useState<string[]>([])
  const [llmModelError, setLlmModelError] = useState('')
  const [embedModelError, setEmbedModelError] = useState('')
  const [ocrModelError, setOcrModelError] = useState('')
  // OpenAI 한방 + Provider 상세(고급) collapse 상태
  const [oneShotKey, setOneShotKey] = useState('')
  const [oneShotError, setOneShotError] = useState('')
  const [oneShotBusy, setOneShotBusy] = useState(false)
  const [showProviderAdvanced, setShowProviderAdvanced] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [scopeError, setScopeError] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [holidayInput, setHolidayInput] = useState('')

  const schedule = ((config as TenantConfigOut).hitl_schedule || {}) as Record<string, DaySchedule>
  const holidays = ((config as TenantConfigOut).hitl_holidays || []) as string[]
  const setDay = (key: string, patch: DaySchedule) =>
    setConfig(c => {
      const sched = ((c as TenantConfigOut).hitl_schedule || {}) as Record<string, DaySchedule>
      return { ...c, hitl_schedule: { ...sched, [key]: { ...(sched[key] || {}), ...patch } } } as TenantConfigOut
    })
  const addHoliday = (d: string) => {
    if (d && !holidays.includes(d)) setConfig(c => ({ ...c, hitl_holidays: [...holidays, d] }) as TenantConfigOut)
  }
  const removeHoliday = (d: string) =>
    setConfig(c => ({ ...c, hitl_holidays: holidays.filter(x => x !== d) }) as TenantConfigOut)

  const loadLlmModels = async () => {
    setLlmModelError('')
    try {
      setLlmModels(await fetchProviderModels('llm', config.llm_provider_type, config.llm_base_url, config.llm_api_key, config.model_id))
    } catch { setLlmModelError('LLM 모델 조회 실패 — Base URL / API Key를 확인하세요') }
  }
  const loadEmbedModels = async () => {
    setEmbedModelError('')
    try {
      setEmbedModels(await fetchProviderModels('embed', config.embed_provider_type, config.embed_base_url, config.embed_api_key, config.embed_model))
    } catch { setEmbedModelError('Embedding 모델 조회 실패 — Base URL / API Key를 확인하세요') }
  }
  const loadOcrModels = async () => {
    setOcrModelError('')
    try {
      setOcrModels(await fetchProviderModels('ocr', config.ocr_provider_type, config.ocr_base_url, config.ocr_api_key, config.ocr_model))
    } catch { setOcrModelError('OCR 모델 조회 실패 — Base URL / API Key를 확인하세요') }
  }
  // OpenAI 키 1개로 3종(LLM·Embedding·OCR)을 기본값으로 한 번에 설정한다(한방).
  const handleQuickSetup = async () => {
    setOneShotError(''); setOneShotBusy(true)
    try {
      const updated = await quickSetupOpenAI(oneShotKey.trim())
      setConfig(updated as TenantConfigOut)   // 3종이 openai가 됨 → 요약 카드로 전환
      setOneShotKey('')
    } catch { setOneShotError('설정 실패 — OpenAI API 키를 확인하세요.') }
    finally { setOneShotBusy(false) }
  }
  const handleSaveSlug = async () => {
    try {
      await updateTenantSlug(slug)
      setSlugSaved(true)
      setTimeout(() => setSlugSaved(false), 2000)
    } catch (e) { alert(e instanceof Error ? e.message : String(e)) }
  }

  // 공개 챗봇 URL·임베드 코드(현재 slug 기준). 복사 버튼용.
  const publicUrl = slug ? `${window.location.origin}/chatbot/${slug}/` : ''
  const iframeCode = `<iframe src="${publicUrl}" width="400" height="600" style="border:none;"></iframe>`
  const copyToClipboard = async (text: string, which: 'url' | 'iframe') => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(which)
      setTimeout(() => setCopied(''), 2000)
    } catch { /* clipboard 거부 시 무시 */ }
  }

  useEffect(() => {
    getTenantConfig().then(data => {
      setConfig(data)
      setSlug(data.slug || '')   // 새로고침 시 저장된 slug를 입력에 복원
      setLoading(false)
    })
  }, [])

  const handleSave = async () => {
    setSaveError(''); setScopeError('')
    // 주제범위 제어를 켜려면 응대 범위가 있어야 한다(빈 채로 켜면 백엔드가 400으로 막는다 — 미리 안내).
    if (config.topic_scope_enabled && !(config.scope_description || '').trim()) {
      setScopeError('주제범위 제어를 켜려면 응대 범위를 입력하세요.')
      return
    }
    try {
      await updateTenantConfig(config)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch { setSaveError('저장 실패 — Provider 연결 검증에 실패했을 수 있습니다(Base URL / API Key 확인)') }
  }
  const handleResetKey = async () => {
    if (!resetConfirm) { setResetConfirm(true); return }
    try {
      const data = await resetTenantKey()
      setNewKey(data.new_tenant_key)
      setResetConfirm(false)
    } catch { alert('재발급에 실패했습니다.'); setResetConfirm(false) }
  }

  if (loading) return <p className="text-sm text-muted-foreground">로딩 중...</p>

  const intro = SECTIONS.find(s => s.id === section)?.intro

  // Provider 상태: 미설정(LLM 빈값) / 3종 동일 타입 / 섞임. 상단 카드·고급 collapse 결정.
  const llmType = config.llm_provider_type || ''
  const isUnset = llmType === ''
  const allSameProvider = !isUnset && llmType === (config.embed_provider_type || '') && llmType === (config.ocr_provider_type || '')
  const isMixedProvider = !isUnset && !allSameProvider
  const providerAdvancedOpen = isMixedProvider || showProviderAdvanced
  const providerLabel = (t: string) => ({ openai: 'OpenAI', anthropic: 'Claude', custom: 'Custom' } as Record<string, string>)[t] || '기본'

  return (
    <div className="max-w-3xl">
      <div role="tablist" className="mb-1 flex flex-wrap gap-x-1 border-b border-border">
        {SECTIONS.map(s => (
          <button key={s.id} role="tab" aria-selected={section === s.id} onClick={() => setSection(s.id)}
            className={cn('shrink-0 whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors',
              section === s.id ? 'border-primary font-medium text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground')}>
            {s.label}
          </button>
        ))}
      </div>
      {intro && <p className="mb-5 mt-3 text-xs text-muted-foreground">{intro}</p>}

      {/* ── 일반 ─────────────────────────────────────────── */}
      {section === 'general' && (
        <div className="space-y-6">
          <div className="space-y-2">
            <Label>브랜드 텍스트</Label>
            <Input aria-label="브랜드 텍스트" value={config.brand_name || ''}
              onChange={e => setConfig(c => ({ ...c, brand_name: e.target.value }))}
              placeholder="위젯 헤더 상단에 표시 (비우면 상태 텍스트만)" />
            <p className={hint}>위젯 상단에 보일 가게/서비스 이름이에요.</p>
          </div>
          <div className="space-y-2">
            <Label>환영 메시지</Label>
            <Textarea value={config.welcome_message}
              onChange={e => setConfig(c => ({ ...c, welcome_message: e.target.value }))}
              placeholder="위젯이 열릴 때 방문자에게 표시될 메시지 (비워두면 표시 안 함)" />
            <p className={hint}>위젯이 열릴 때 손님에게 보여줄 첫인사예요.</p>
          </div>
          <div className="space-y-2">
            <Label>Base System Prompt</Label>
            <Textarea data-testid="system-prompt-input" className="min-h-[200px] font-mono text-xs"
              value={config.system_prompt} onChange={e => setConfig(c => ({ ...c, system_prompt: e.target.value }))} />
            <p className={hint}>봇의 성격·말투·역할을 정하는 지시문이에요. 예: "친절한 쇼핑몰 상담원처럼 존댓말로 답하라."</p>
          </div>
          <div className="space-y-2">
            <Label>상담원 표시 이름</Label>
            <Input value={config.agent_display_name}
              onChange={e => setConfig(c => ({ ...c, agent_display_name: e.target.value }))} placeholder="상담원" />
            <p className={hint}>사람 상담원으로 전환됐을 때 손님에게 보일 이름이에요.</p>
          </div>

          {/* ── 주제범위 제어 ── */}
          <div className="space-y-2 border-t border-border pt-6">
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 rounded border-input" aria-label="주제범위 제어"
                checked={!!config.topic_scope_enabled}
                onChange={e => setConfig(c => ({ ...c, topic_scope_enabled: e.target.checked }))} />
              주제범위 제어 (응대 범위 밖 질문 거절)
            </label>
            <p className={hint}>켜면 봇이 아래 "응대 범위" 밖 질문(예: 일반 상식)을 정중히 거절해요. 끄면 무엇이든 답합니다.</p>
            {config.topic_scope_enabled && (
              <div className="space-y-4 border-l-2 border-border pl-3 pt-2">
                <div className="space-y-2">
                  <Label>응대 범위</Label>
                  <Textarea aria-label="응대 범위" value={config.scope_description || ''}
                    onChange={e => setConfig(c => ({ ...c, scope_description: e.target.value }))}
                    placeholder="예: 주문·배송·반품·상품 문의" />
                  <p className={hint}>봇이 답해도 되는 주제예요. 이 범위 밖 질문은 거절합니다. (켜려면 필수)</p>
                  {scopeError && <p className={errorCls}>{scopeError}</p>}
                </div>
                <div className="space-y-2">
                  <Label>거절 문구 (선택)</Label>
                  <Input aria-label="거절 문구" value={config.scope_refusal_message || ''}
                    onChange={e => setConfig(c => ({ ...c, scope_refusal_message: e.target.value }))}
                    placeholder="비우면 응대 범위를 인용한 표준 문구로 거절" />
                  <p className={hint}>범위 밖 질문에 보낼 거절 메시지예요. 비우면 자동으로 만들어 줍니다.</p>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── AI 모델 ───────────────────────────────────────── */}
      {section === 'ai' && (
        <div className="space-y-6">
          {/* 미설정 → OpenAI 한방 카드 */}
          {isUnset && (
            <div className="space-y-3 rounded-lg border border-primary/40 bg-primary/5 p-4">
              <div>
                <h3 className="text-sm font-semibold">빠른 시작 — OpenAI 키로 한 번에 설정</h3>
                <p className="text-xs text-muted-foreground">OpenAI API 키 하나면 챗·문서검색·이미지OCR이 모두 켜집니다. {config.platform_default_providers_enabled ? '개발 환경은 키 없이 아래 고급에서 기본 제공자로도 됩니다.' : '키를 넣기 전에는 체험 한도 안에서 플랫폼 키로 답합니다.'}</p>
              </div>
              <Input type="password" aria-label="OpenAI API Key" value={oneShotKey}
                onChange={e => setOneShotKey(e.target.value)} placeholder="sk-..." />
              {oneShotError && <p className={errorCls}>{oneShotError}</p>}
              <Button type="button" disabled={oneShotBusy || !oneShotKey.trim()} onClick={handleQuickSetup}>
                {oneShotBusy ? '설정 중…' : '시작하기'}
              </Button>
            </div>
          )}
          {/* 3종 동일 → 컴팩트 요약 카드 */}
          {allSameProvider && (
            <div className="rounded-lg border border-border bg-card p-4 text-sm">
              <span className="font-medium">AI 제공자: {providerLabel(llmType)}</span>
              <span className="ml-2 text-muted-foreground">· 챗 {config.model_id} · 임베딩 {config.embed_model || '(미설정)'} · OCR {config.ocr_model || '(미설정)'}</span>
            </div>
          )}
          {/* 고급 설정 토글(섞임이면 항상 펼침이라 토글 숨김) */}
          {!isMixedProvider && (
            <Button type="button" variant="ghost" size="sm" className="px-0 text-muted-foreground hover:bg-transparent"
              onClick={() => setShowProviderAdvanced(v => !v)}>{providerAdvancedOpen ? '▾' : '▸'} 고급 설정 (Provider 상세)</Button>
          )}

          <div className={providerAdvancedOpen ? 'space-y-6' : 'hidden'}>
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold">LLM Provider (비용 부담)</h3>
              <p className="text-xs text-muted-foreground">손님과 대화하고 자료를 정리하는 핵심 AI예요. {config.platform_default_providers_enabled ? '미설정 시 플랫폼 기본(OpenRouter)을 씁니다.' : '넣기 전에는 체험 한도(대화 턴) 안에서 플랫폼 키로 답합니다.'}</p>
            </div>
            <div className="space-y-2">
              <Label>LLM Provider 타입</Label>
              <Select aria-label="LLM Provider 타입" value={config.llm_provider_type || ''}
                onChange={e => { const v = e.target.value; setConfig(c => ({ ...c, llm_provider_type: v, llm_base_url: v === 'custom' ? c.llm_base_url : '' })) }}>
                {config.platform_default_providers_enabled && <option value="">기본 (dev: OpenRouter)</option>}
                <option value="openai">OpenAI</option>
                <option value="anthropic">Claude (Anthropic)</option>
                <option value="custom">Custom (OpenAI-호환)</option>
              </Select>
              <p className={hint}>어떤 AI 회사 서비스를 쓸지 골라요(OpenAI·Claude 등). "기본"은 개발 환경이 제공하는 모델이에요.</p>
            </div>
            {config.llm_provider_type === 'custom' && (
              <div className="space-y-2">
                <Label>LLM Base URL</Label>
                <Input aria-label="LLM Base URL" value={config.llm_base_url || ''}
                  onChange={e => setConfig(c => ({ ...c, llm_base_url: e.target.value }))} placeholder="예: https://openrouter.ai/api/v1" />
                <p className={hint}>Custom(직접 호스팅·기타 서비스)일 때만 그 주소를 넣어요.</p>
              </div>
            )}
            <div className="space-y-2">
              <Label>LLM API Key</Label>
              <Input type="password" aria-label="LLM API Key" value={config.llm_api_key || ''}
                onChange={e => setConfig(c => ({ ...c, llm_api_key: e.target.value }))} placeholder="설정됨이면 ******** (변경할 때만 입력)" />
              <p className={hint}>AI 서비스에서 발급받은 비밀 키예요. 모르면 담당 개발자에게 "OpenAI(또는 Claude) API 키"라고 요청하세요.</p>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" type="button" onClick={loadLlmModels}>LLM 모델 불러오기</Button>
                <span className="text-xs text-muted-foreground">provider에서 사용가능 모델을 조회해 아래 AI 모델 목록에 채웁니다.</span>
              </div>
              {llmModelError && <p className={errorCls}>{llmModelError}</p>}
            </div>
            <div className="space-y-2">
              <Label>AI 모델</Label>
              <Select aria-label="AI 모델" value={config.model_id} onChange={e => setConfig(c => ({ ...c, model_id: e.target.value }))}>
                {modelOptions(llmModels, config.model_id).map(m => <option key={m} value={m}>{m}</option>)}
              </Select>
              <Input aria-label="AI 모델 직접 입력" className="text-xs" value={config.model_id}
                onChange={e => setConfig(c => ({ ...c, model_id: e.target.value }))} placeholder="직접 입력(목록에 없는 모델)" />
              <p className={hint}>손님과 대화하고, 올린 자료도 이 AI가 정리합니다.</p>
            </div>
            <Button type="button" variant="ghost" size="sm" className="px-0 text-muted-foreground hover:bg-transparent"
              onClick={() => setShowAdvanced(v => !v)}>{showAdvanced ? '▾' : '▸'} 자료 정리 모델</Button>
            {showAdvanced && (
              <div className="space-y-2 border-l-2 border-border pl-3">
                <Label>자료 정리 모델</Label>
                <Select aria-label="자료 정리 모델" value={config.extraction_model || ''}
                  onChange={e => setConfig(c => ({ ...c, extraction_model: e.target.value }))}>
                  <option value="">(대화 모델과 동일)</option>
                  {modelOptions(llmModels, config.extraction_model).map(m => <option key={m} value={m}>{m}</option>)}
                </Select>
                <Input aria-label="자료 정리 모델 직접 입력" className="text-xs" value={config.extraction_model || ''}
                  onChange={e => setConfig(c => ({ ...c, extraction_model: e.target.value }))} placeholder="비우면 대화 모델과 동일" />
                <p className={hint}>문서를 올릴 때 내용을 정리하는 모델입니다. 보통 비워두면 됩니다.</p>
              </div>
            )}
          </div>

          <div className="space-y-4 border-t border-border pt-6">
            <div>
              <h3 className="text-sm font-semibold">Embedding Provider</h3>
              <p className="text-xs text-muted-foreground">올린 문서를 AI가 검색하도록 "숫자 지문"으로 바꾸는 엔진이에요. 보통 LLM과 같은 회사 걸 쓰면 됩니다. {config.platform_default_providers_enabled ? '미설정 시 플랫폼 기본(ollama)을 씁니다.' : 'LLM 키 없이 체험 중일 때만 플랫폼 키로 돌고, LLM 키를 넣으면 이것도 설정해야 합니다.'}</p>
            </div>
            <div className="space-y-2">
              <Label>Embedding Provider 타입</Label>
              <Select aria-label="Embedding Provider 타입" value={config.embed_provider_type || ''}
                onChange={e => { const v = e.target.value; setConfig(c => ({ ...c, embed_provider_type: v, embed_base_url: v === 'custom' ? c.embed_base_url : '' })) }}>
                {config.platform_default_providers_enabled && <option value="">기본 (dev: ollama)</option>}
                <option value="openai">OpenAI</option>
                <option value="custom">Custom (OpenAI-호환)</option>
              </Select>
              <p className={hint}>문서 검색용 임베딩 엔진을 골라요. "기본"은 개발 환경이 제공하는 모델이에요.</p>
            </div>
            {config.embed_provider_type === 'custom' && (
              <div className="space-y-2">
                <Label>Embedding Base URL</Label>
                <Input aria-label="Embedding Base URL" value={config.embed_base_url || ''}
                  onChange={e => setConfig(c => ({ ...c, embed_base_url: e.target.value }))} />
              </div>
            )}
            <div className="space-y-2">
              <Label>Embedding API Key</Label>
              <Input type="password" aria-label="Embedding API Key" value={config.embed_api_key || ''}
                onChange={e => setConfig(c => ({ ...c, embed_api_key: e.target.value }))} placeholder="설정됨이면 ******** (변경할 때만 입력)" />
            </div>
            <div>
              <Button size="sm" variant="outline" type="button" onClick={loadEmbedModels}>Embedding 모델 불러오기</Button>
              {embedModelError && <p className={errorCls}>{embedModelError}</p>}
            </div>
            <div className="space-y-2">
              <Label>Embedding 모델</Label>
              <Select aria-label="Embedding 모델" value={config.embed_model || ''}
                onChange={e => setConfig(c => ({ ...c, embed_model: e.target.value }))}>
                <option value="">(선택)</option>
                {modelOptions(embedModels, config.embed_model).map(m => <option key={m} value={m}>{m}</option>)}
              </Select>
              <Input aria-label="Embedding 모델 직접 입력" className="text-xs" value={config.embed_model || ''}
                onChange={e => setConfig(c => ({ ...c, embed_model: e.target.value }))} placeholder="직접 입력(목록에 없는 모델)" />
            </div>
            <div className="space-y-2">
              <Label>Embedding 차원</Label>
              <Input type="number" aria-label="Embedding 차원" className="w-40" value={config.embed_dim ?? 1024}
                onChange={e => setConfig(c => ({ ...c, embed_dim: Number(e.target.value) }))} />
              <p className={hint}>임베딩 모델이 만드는 "숫자 지문"의 길이예요. 모델 기본값을 쓰면 됩니다(예: 1024).</p>
            </div>
          </div>

          <div className="space-y-4 border-t border-border pt-6">
            <div>
              <h3 className="text-sm font-semibold">OCR(Vision) Provider</h3>
              <p className="text-xs text-muted-foreground">이미지·스캔 PDF의 글자를 읽어들이는 vision 모델이에요(예: GPT-4o, Claude, Gemini). 미설정 시 이미지·스캔 문서 업로드는 막힙니다(이미지를 쓰면 설정 필요).</p>
            </div>
            <div className="space-y-2">
              <Label>OCR Provider 타입</Label>
              <Select aria-label="OCR Provider 타입" value={config.ocr_provider_type || ''}
                onChange={e => { const v = e.target.value; setConfig(c => ({ ...c, ocr_provider_type: v, ocr_base_url: v === 'custom' ? c.ocr_base_url : '' })) }}>
                <option value="">(미설정)</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Claude (Anthropic)</option>
                <option value="custom">Custom (OpenAI-호환)</option>
              </Select>
              <p className={hint}>이미지 OCR에 쓸 vision 모델을 골라요. "기본"은 개발 환경이 제공하는 모델이에요.</p>
            </div>
            {config.ocr_provider_type === 'custom' && (
              <div className="space-y-2">
                <Label>OCR Base URL</Label>
                <Input aria-label="OCR Base URL" value={config.ocr_base_url || ''}
                  onChange={e => setConfig(c => ({ ...c, ocr_base_url: e.target.value }))} />
              </div>
            )}
            <div className="space-y-2">
              <Label>OCR API Key</Label>
              <Input type="password" aria-label="OCR API Key" value={config.ocr_api_key || ''}
                onChange={e => setConfig(c => ({ ...c, ocr_api_key: e.target.value }))} placeholder="설정됨이면 ******** (변경할 때만 입력)" />
            </div>
            <div>
              <Button size="sm" variant="outline" type="button" onClick={loadOcrModels}>OCR 모델 불러오기</Button>
              {ocrModelError && <p className={errorCls}>{ocrModelError}</p>}
            </div>
            <div className="space-y-2">
              <Label>OCR 모델</Label>
              <Select aria-label="OCR 모델" value={config.ocr_model || ''}
                onChange={e => setConfig(c => ({ ...c, ocr_model: e.target.value }))}>
                <option value="">(선택)</option>
                {modelOptions(ocrModels, config.ocr_model).map(m => <option key={m} value={m}>{m}</option>)}
              </Select>
              <Input aria-label="OCR 모델 직접 입력" className="text-xs" value={config.ocr_model || ''}
                onChange={e => setConfig(c => ({ ...c, ocr_model: e.target.value }))} placeholder="직접 입력(vision 가능 모델)" />
            </div>
          </div>
          </div>
        </div>
      )}

      {/* ── 상담 전환(HITL) ───────────────────────────────── */}
      {section === 'handoff' && (
        <div className="space-y-6">
          <div>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 rounded border-input" checked={!!config.hitl_enabled}
                onChange={e => setConfig(c => ({ ...c, hitl_enabled: e.target.checked }))} />
              HITL 사용
            </label>
            <p className={hint}>끄면 AI 전용으로 운영되며 상담원 전환(escalation)이 발생하지 않습니다.</p>
          </div>
          <div className="space-y-2">
            <Label>웹훅 유형</Label>
            <Select aria-label="웹훅 유형 선택" value={config.webhook_type}
              onChange={e => setConfig(c => ({ ...c, webhook_type: e.target.value }))}>
              {WEBHOOK_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
            </Select>
            <p className={hint}>상담 요청이 생기면 Slack/Discord 등으로 알림을 보낼 곳이에요(선택).</p>
          </div>
          {config.webhook_type && (
            <div className="space-y-2">
              <Label>웹훅 URL</Label>
              <Input value={config.webhook_url} onChange={e => setConfig(c => ({ ...c, webhook_url: e.target.value }))}
                placeholder="https://hooks.slack.com/..." />
            </div>
          )}

          {/* ── 상담 가능 시간(영업시간) ─ HITL 켜진 경우에만 ── */}
          {config.hitl_enabled && (
            <div className="space-y-4 border-t border-border pt-6">
              <div>
                <h3 className="text-sm font-semibold">상담 가능 시간 (영업시간)</h3>
                <p className="text-xs text-muted-foreground">상담원이 응대 가능한 요일·시간이에요. 이 시간 외에는 AI만 답하고 상담원 자동 연결은 일어나지 않습니다. 비워두면 24시간 항상 연결돼요.</p>
              </div>
              <div className="space-y-2">
                <Label>표준시간대 (타임존)</Label>
                <Input aria-label="타임존" className="w-64" value={config.hitl_timezone || ''}
                  onChange={e => setConfig(c => ({ ...c, hitl_timezone: e.target.value }))} placeholder="예: Asia/Seoul" />
                <p className={hint}>시간을 어느 지역 기준으로 볼지예요. 한국이면 Asia/Seoul.</p>
              </div>
              <div className="space-y-1.5">
                <Label>요일별 시간</Label>
                {WEEKDAYS.map(([key, label]) => {
                  const day = schedule[key] || {}
                  return (
                    <div key={key} className="flex items-center gap-2 text-sm">
                      <label className="flex w-14 cursor-pointer items-center gap-1.5">
                        <input type="checkbox" aria-label={`${label} 영업`} className="h-4 w-4 rounded border-input"
                          checked={!!day.enabled} onChange={e => setDay(key, { enabled: e.target.checked })} />
                        {label}
                      </label>
                      <Input aria-label={`${label} 시작`} type="time" className="w-32" value={day.start || ''}
                        disabled={!day.enabled} onChange={e => setDay(key, { start: e.target.value })} />
                      <span className="text-muted-foreground">~</span>
                      <Input aria-label={`${label} 종료`} type="time" className="w-32" value={day.end || ''}
                        disabled={!day.enabled} onChange={e => setDay(key, { end: e.target.value })} />
                    </div>
                  )
                })}
              </div>
              <div className="space-y-2">
                <Label>휴일 (쉬는 날)</Label>
                <div className="flex items-center gap-2">
                  <Input aria-label="휴일 추가" type="date" className="w-44" value={holidayInput}
                    onChange={e => setHolidayInput(e.target.value)} />
                  <Button size="sm" variant="outline" type="button"
                    onClick={() => { addHoliday(holidayInput); setHolidayInput('') }}>추가</Button>
                </div>
                {holidays.length > 0 && (
                  <ul className="flex flex-wrap gap-1.5">
                    {holidays.map(d => (
                      <li key={d}>
                        <Badge variant="secondary" className="gap-1">{d}
                          <button type="button" aria-label={`휴일 ${d} 삭제`} className="ml-1 leading-none"
                            onClick={() => removeHoliday(d)}>×</button>
                        </Badge>
                      </li>
                    ))}
                  </ul>
                )}
                <p className={hint}>공휴일·정기휴무처럼 요일과 상관없이 쉬는 날이에요. 그날은 상담원 연결이 꺼집니다.</p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── 공개 URL·보안 ─────────────────────────────────── */}
      {section === 'billing' && <PricingSection />}

      {section === 'security' && (
        <div className="space-y-6">
          <div className="space-y-2">
            <Label>Tenant Slug (공개 챗봇 URL)</Label>
            {isAdmin ? (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-muted-foreground">/chatbot/</span>
                  <Input aria-label="Tenant Slug" className="w-64" value={slug} onChange={e => setSlug(e.target.value)}
                    placeholder="우리가게 · abc-shop" />
                  <span className="text-sm text-muted-foreground">/</span>
                  <Button size="sm" variant="outline" onClick={handleSaveSlug}>{slugSaved ? '✓ 저장됨' : 'Slug 저장'}</Button>
                </div>
                <p className={hint}>손님이 접속하는 공개 챗봇 주소의 일부예요. 한글·영문·숫자·하이픈을 쓸 수 있어요(예: 우리가게). 변경하면 사이트에 박아둔 기존 임베드 URL이 끊깁니다.</p>
              </>
            ) : (
              <p className={hint}>Slug 변경은 Admin만 할 수 있습니다.</p>
            )}
            {slug && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <code className="flex-1 truncate rounded bg-muted px-2 py-1 text-xs">{publicUrl}</code>
                  <Button size="sm" variant="outline" type="button" onClick={() => copyToClipboard(publicUrl, 'url')}>
                    {copied === 'url' ? '✓ 복사됨' : 'URL 복사'}
                  </Button>
                  <Button size="sm" variant="outline" type="button" onClick={() => copyToClipboard(iframeCode, 'iframe')}>
                    {copied === 'iframe' ? '✓ 복사됨' : '임베드 코드 복사'}
                  </Button>
                </div>
                <p className={hint}>저장된 slug 기준입니다. 변경 후엔 먼저 Slug를 저장하세요.</p>
              </div>
            )}
          </div>
          <div>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 rounded border-input" aria-label="visitor_id 신원검증 요구"
                checked={!!config.require_identity_verification}
                onChange={e => setConfig(c => ({ ...c, require_identity_verification: e.target.checked }))} />
              visitor_id 신원검증 요구 (HMAC)
            </label>
            <p className={hint}>켜면 식별 방문자는 HMAC 해시가 있어야 연결됩니다(위조 방지). 익명은 영향 없음. 모르면 꺼두세요.</p>
          </div>
          {isAdmin && (
          <div className="space-y-3 border-t border-border pt-6">
            <div>
              <h3 className="text-sm font-semibold">API KEY 재발급</h3>
              <p className="text-xs text-muted-foreground">서버 연동용 비밀 키예요. 재발급 즉시 기존 KEY는 무효화되니 서버 설정을 바로 업데이트해야 합니다.</p>
            </div>
            {newKey ? (
              <Card className="border-primary bg-primary/15">
                <CardContent className="space-y-2 pt-5">
                  <p className="text-xs font-semibold text-foreground">새 API KEY (지금만 표시됩니다. 반드시 복사하세요.)</p>
                  <code className="block break-all text-sm">{newKey}</code>
                  <div className="flex gap-2">
                    <Button size="sm" onClick={() => navigator.clipboard.writeText(newKey)}>복사</Button>
                    <Button size="sm" variant="outline" onClick={() => setNewKey(null)}>확인 완료</Button>
                  </div>
                </CardContent>
              </Card>
            ) : (
              <div className="flex items-center gap-3">
                <Button variant={resetConfirm ? 'destructive' : 'outline'} onClick={handleResetKey}>
                  {resetConfirm ? '정말 재발급하시겠습니까? (클릭 시 즉시 실행)' : 'API KEY 재발급'}
                </Button>
                {resetConfirm && <Button size="sm" variant="ghost" onClick={() => setResetConfirm(false)}>취소</Button>}
              </div>
            )}
          </div>
          )}
        </div>
      )}

      {/* 항상 보이는 atomic 저장 (Slug 저장·키 재발급은 별도) */}
      {section !== 'billing' && (
        <div className="mt-8 space-y-2 border-t border-border pt-6">
          {saveError && <p className={errorCls}>{saveError}</p>}
          <Button onClick={handleSave}>{saved ? '✓ 저장됨' : '저장'}</Button>
          <p className={hint}>모든 탭의 변경을 한 번에 저장합니다.</p>
        </div>
      )}
    </div>
  )
}
