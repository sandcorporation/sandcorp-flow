import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Trash2 } from 'lucide-react'
import type { DeployRequestOut, VersionOut } from '../generated/model'
import { useFlowEditorBackend } from '@/lib/flowEditorBackend'
import { HttpError } from '../mutator'
import {
  KINDS, applyOps, flowLevelErrors, kindLabel, newNode,
  type FlowError, type FlowGraph, type FlowNodeJson, type PatchOp,
} from '@/lib/flowGraph'
import { fieldErrors } from '@/lib/nodeForm'
import FlowCanvas from './FlowCanvas'
import VersionsPanel from './VersionsPanel'
import type { Me } from './DeployRequestCard'
import NodeConfigForm from './NodeConfigForm'
import ClaimedNotice from './ClaimedNotice'
import { DecisionLog, TestChat, useTestSession } from './TestPanel'
import { useIsNarrow } from '@/lib/useIsNarrow'
import { useEmbed } from '@/lib/embed'
import { turnPath, type TestTurn } from '@/lib/testTrace'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Textarea } from './ui/textarea'

type SaveState = 'saved' | 'saving' | 'failed'
const EMPTY: FlowGraph = { nodes: [], edges: [] }

/** 편집기: 왼쪽 노드 목록 · 가운데 캔버스 · 오른쪽 패널. 모든 편집은 패치 연산으로 곧바로 초안에 저장한다. */
export default function FlowEditorPage() {
  const { id = '' } = useParams()
  const [searchParams] = useSearchParams()
  const backend = useFlowEditorBackend() // 테넌트 편집기 / 체험(/try) — 경로·인증·숨길 UI 가 여기서 갈린다(#37)
  const isAdmin = backend.useCanEdit()
  const [viewing, setViewing] = useState<{ number: number; graph: FlowGraph } | null>(null)
  const readOnly = !isAdmin || viewing !== null // 버전을 보는 동안은 초안을 고치지 못한다
  const [versions, setVersions] = useState<VersionOut[]>([])
  const [deployedVersion, setDeployedVersion] = useState<number | null>(null)
  const [pendingRequest, setPendingRequest] = useState<DeployRequestOut | null>(null)
  const [requiresApproval, setRequiresApproval] = useState(false)
  const [me, setMe] = useState<Me>(null)
  const [name, setName] = useState('')
  const [graph, setGraph] = useState<FlowGraph>(EMPTY)
  const [errors, setErrors] = useState<FlowError[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [schemas, setSchemas] = useState<Record<string, { config: object }>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const embed = useEmbed() // 임베드(#180): 뷰포트 비례 높이를 쓰지 않는다(iframe 높이 ↔ 높이 전달 순환 방지)
  const narrow = useIsNarrow() // 폰: 캔버스는 읽기만, 노드 설정·시험 대화는 하단 시트(#38)
  const [rightTab, setRightTab] = useState<'test' | 'log' | 'config'>('config')
  const [sheet, setSheet] = useState<'config' | 'test' | 'log' | 'versions' | null>(null)
  const [highlight, setHighlight] = useState<{ nodes: Set<string>; edges: Set<string> } | null>(null)
  const [save, setSave] = useState<{ state: SaveState; message: string }>({ state: 'saved', message: '' })
  const graphRef = useRef(graph)
  graphRef.current = graph
  const queue = useRef<Promise<void>>(Promise.resolve())
  const pending = useRef(0) // 보냈거나 줄 서 있는 저장 묶음 수 — 0이 돼야 「저장됨」
  const sheetRef = useRef<HTMLElement>(null) // 폰 하단 시트 — 캔버스가 고른 노드를 시트 위로 옮길 때 높이를 잰다(#150)
  const spotRef = useRef<(() => { x: number; y: number }) | null>(null)

  const load = useCallback(async (stale?: () => boolean) => {
    const d = await backend.loadFlow(id)
    if (stale?.()) return // 더 새 불러오기가 있다 — 늦은 응답이 그 사이 편집을 덮지 않게 버린다(#167)
    setName(d.flow.name)
    const raw = d.draft.graph as unknown as Partial<FlowGraph>
    setGraph({ nodes: raw.nodes ?? [], edges: raw.edges ?? [] })
    setErrors(d.draft.errors as FlowError[])
    setVersions(d.versions)
    setDeployedVersion(d.flow.deployed_version ?? null)
    setPendingRequest(d.pending_request ?? null)
  }, [id, backend])

  /** 버전·배포 상태만 다시 받는다(초안 그래프는 건드리지 않는다 — 줄 선 저장과 부딪히지 않게). */
  const reloadMeta = useCallback(async () => {
    const d = await backend.loadFlow(id)
    setVersions(d.versions)
    setDeployedVersion(d.flow.deployed_version ?? null)
    setPendingRequest(d.pending_request ?? null)
  }, [id, backend])

  const viewVersion = async (n: number) => {
    try {
      const v = await backend.loadVersion!(id, n)
      setViewing({ number: n, graph: { nodes: v.graph.nodes ?? [], edges: v.graph.edges ?? [] } })
      setSelectedId(null)
    } catch { setSave({ state: 'failed', message: `v${n}을(를) 불러오지 못했습니다.` }) }
  }

  const loadAccount = useCallback(() => {
    backend.loadAccount?.().then((a) => { setMe(a.me); setRequiresApproval(a.requiresApproval) }).catch(() => {})
  }, [backend])
  useEffect(loadAccount, [loadAccount])

  useEffect(() => {
    // 폼 틀(종류별 JSON 스키마). 못 받아도 「고급」 JSON 탭으로 편집할 수 있다.
    backend.nodeSchemas().then(setSchemas).catch(() => {})
  }, [backend])

  useEffect(() => {
    // StrictMode(개발 서버)는 이 효과를 두 번 돌린다 — 앞 응답만 먼저 와서 화면이 열린 뒤 뒤 응답이 편집을 지우던 경합(#167).
    let cancelled = false
    load(() => cancelled).then(() => { if (!cancelled) setLoaded(true) }).catch(() => { if (!cancelled) setLoadError('흐름을 불러오지 못했습니다.') })
    return () => { cancelled = true }
  }, [load])

  /** 화면에 먼저 반영하고, 연산을 순서대로 하나씩 보낸다. 거절되면 서버 초안으로 다시 맞춘다. */
  const commit = useCallback((ops: PatchOp[]) => {
    if (readOnly || ops.length === 0) return
    setGraph((g) => applyOps(g, ops))
    pending.current += 1
    setSave({ state: 'saving', message: '' })
    queue.current = queue.current.then(async () => {
      try {
        const r = await backend.patchDraft(id, ops)
        setErrors(r.errors)
        pending.current -= 1
        if (pending.current === 0) setSave((cur) => (cur.state === 'failed' ? cur : { state: 'saved', message: '' }))
      } catch (err) {
        const body = err instanceof HttpError ? (err.body as { detail?: string } | undefined) : undefined
        pending.current -= 1
        await load().catch(() => {})
        setSave({ state: 'failed', message: `저장하지 못했습니다${body?.detail ? `: ${body.detail}` : '.'} 서버의 초안으로 다시 맞췄습니다.` })
      }
    })
  }, [id, load, readOnly, backend])

  const testSession = useTestSession(id, useCallback(() => queue.current, []))
  const focusTurn = (turn: TestTurn, nodeId: string | null) => {
    setHighlight(turnPath(turn.entries))
    if (nodeId && graphRef.current.nodes.some((n) => n.id === nodeId)) setSelectedId(nodeId)
  }

  const addNode = (kind: string, at?: { x: number; y: number }) => {
    const g = graphRef.current
    const position = at ?? spotRef.current?.() ?? { x: 40 + (g.nodes.length % 4) * 240, y: 40 }
    const n = newNode(g, kind, position)
    commit([{ op: 'upsert_node', id: n.id, kind: n.kind, code: n.code, name: n.name, position: n.position, config: n.config }])
    setSelectedId(n.id)
  }

  const shownGraph = viewing?.graph ?? graph
  const shownErrors = viewing ? [] : errors
  const selected = shownGraph.nodes.find((n) => n.id === selectedId) ?? null
  const flowErrors = flowLevelErrors(errors)
  const claimed = searchParams.get('claimed') === '1'

  const tab = narrow ? sheet : rightTab
  const versionsBody = backend.versionsPanel ? (
    <VersionsPanel
      flowId={id} versions={versions} deployedVersion={deployedVersion} pending={pendingRequest}
      requiresApproval={requiresApproval} canDeploy={isAdmin} me={me} testedTurns={testSession.turns.length}
      viewing={viewing?.number ?? null} onView={(n) => { viewVersion(n); setSheet(null) }}
      onChanged={() => { reloadMeta().catch(() => {}); loadAccount() }}
    />
  ) : (
    <p className="text-xs text-muted-foreground">{backend.versionsNote}</p>
  )
  const panelBody = (
    <>
      {tab === 'test' && <TestChat {...testSession} readOnly={readOnly} />}
      {tab === 'log' && <DecisionLog turns={testSession.turns} graph={shownGraph} onFocus={focusTurn} />}
      {tab === 'config' && (selected ? (
        <NodePanel key={selected.id} node={selected} schema={schemas[selected.kind]?.config} errors={errors} readOnly={readOnly} canDelete={!narrow} commit={commit} onDeleted={() => setSelectedId(null)} />
      ) : (
        <Overview errors={errors} graph={graph} onPick={(nid) => { setSelectedId(nid); if (narrow) setSheet('config') }} />
      ))}
      {tab === 'versions' && versionsBody}
    </>
  )
  const canvas = (
    <FlowCanvas
      key={viewing ? `v${viewing.number}` : 'draft'}
      graph={shownGraph}
      errors={shownErrors}
      selectedId={selectedId}
      readOnly={readOnly || narrow}
      spotRef={spotRef}
      coverRef={narrow ? sheetRef : undefined}
      activeNodeIds={tab === 'log' ? highlight?.nodes : undefined}
      activeEdgeKeys={tab === 'log' ? highlight?.edges : undefined}
      onSelect={(nid) => {
        setSelectedId(nid)
        if (narrow) setSheet(nid ? 'config' : (s) => (s === 'config' ? null : s))
        else if (nid) setRightTab('config')
      }}
      onMoved={(nid, position) => commit([{ op: 'upsert_node', id: nid, position }])}
      onConnect={(from, handle, to) => commit([{ op: 'connect', from, to, handle }])}
      onDelete={(ids, cuts) => {
        commit([...ids.map((x): PatchOp => ({ op: 'delete_node', id: x })), ...cuts.map((c): PatchOp => ({ op: 'disconnect', from: c.from, handle: c.handle }))])
        if (selectedId && ids.includes(selectedId)) setSelectedId(null)
      }}
      onDropKind={(kind, position) => addNode(kind, { x: Math.round(position.x), y: Math.round(position.y) })}
    />
  )
  const viewingBar = viewing && (
    <div className="absolute inset-x-0 top-0 z-10 flex flex-wrap items-center gap-2 border-b border-ink bg-raised px-3 py-1.5 text-sm" role="status">
      <span className="font-semibold text-ink">v{viewing.number} 보는 중</span>
      <span className="text-muted-foreground">읽기 전용 — 저장된 버전은 고칠 수 없습니다</span>
      <Button type="button" size="sm" variant="outline" className="ml-auto h-7" onClick={() => setViewing(null)}>초안으로 돌아가기</Button>
    </div>
  )

  if (loadError) return <p role="alert" className="text-sm text-destructive">{loadError}</p>
  if (!loaded) return <div aria-busy="true" />

  return (
    <div className="space-y-3 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link to={backend.back.to} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-ink"><ArrowLeft className="h-4 w-4" />{backend.back.label}</Link>
        <h1 className="font-cond text-lg font-semibold tracking-wide text-ink">{name}</h1>
        {backend.draftStatus && (
          <>
            <span className="hmi-label flex items-center gap-1.5" role="status">
              <span className={`lamp ${save.state === 'saving' ? 'lamp-run' : save.state === 'failed' ? 'lamp-alarm' : 'lamp-ok'}`} />
              {save.state === 'saving' ? '저장 중' : save.state === 'failed' ? '저장 실패' : '초안 저장됨'}
            </span>
            <span className="hmi-label">{errors.length === 0 ? '검증 통과' : `검증 오류 ${errors.length}건`}</span>
          </>
        )}
        {!isAdmin && <span className="hmi-label border border-line-soft px-1.5 py-0.5">{backend.readOnlyNotice}</span>}
        {deployedVersion !== null && <span className="hmi-label flex items-center gap-1.5"><span className="lamp lamp-ok" />v{deployedVersion} 배포 중</span>}
      </div>
      {claimed && loaded && <ClaimedNotice graph={graph} />}
      {flowErrors.map((e, i) => <p key={i} role="alert" className="text-sm text-destructive">{e.message}</p>)}
      {save.state === 'failed' && <p role="alert" className="text-sm text-destructive">{save.message}</p>}

      {narrow ? (
        <>
          {/* 15rem = 머리줄·여백·제목줄(상태 표시가 붙어 두 줄로 접힐 수 있다)·아래 탭 자리. 13rem 이던 때 27px 넘쳐 문서가 끌렸다(#160) */}
          <div className={`relative ${embed ? 'h-[480px]' : 'h-[calc(100dvh-15rem)] min-h-[300px]'} min-w-0 overflow-hidden border border-line-soft bg-background`} data-testid="flow-canvas">
            {viewingBar}
            {canvas}
          </div>
          <div className="h-12" aria-hidden="true" />
          {sheet && (
            <section ref={sheetRef} role="dialog" aria-label={SHEET_LABEL[sheet]} data-testid="phone-sheet" className="fixed inset-x-0 bottom-12 z-40 flex max-h-[62dvh] flex-col border-t-2 border-ink bg-surface shadow-lg">
              <div className="flex items-center justify-between border-b border-line-soft px-3 py-1.5">
                <h2 className="hmi-label">{SHEET_LABEL[sheet]}</h2>
                <button type="button" aria-label="시트 닫기" className="px-2 text-lg leading-none text-muted-foreground hover:text-ink" onClick={() => setSheet(null)}>×</button>
              </div>
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-3">{panelBody}</div>
            </section>
          )}
          <nav aria-label="하단 탭" className="fixed inset-x-0 bottom-0 z-50 flex h-12 border-t-2 border-ink bg-head text-head-foreground">
            {([['config', '노드 설정'], ['test', '시험 대화'], ['log', '판단 기록'], ['versions', '버전']] as const)
              .filter(([k]) => (k === 'config') || (k === 'versions' ? backend.versionsPanel : backend.testChat))
              .map(([k, label]) => (
                <button
                  key={k} type="button" aria-pressed={sheet === k} onClick={() => setSheet(sheet === k ? null : k)}
                  className={`min-w-0 flex-1 truncate px-1 text-sm ${sheet === k ? 'border-t-2 border-run font-semibold' : 'opacity-80'}`}
                >{label}</button>
              ))}
          </nav>
        </>
      ) : (
      <div data-embed-grid={embed || undefined} className={`grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)_300px] ${embed ? 'lg:h-[640px]' : 'lg:min-h-[480px] lg:flex-1'}`}>
        <aside className="min-w-0 space-y-4 border border-line-soft bg-surface p-3 lg:flex lg:min-h-0 lg:flex-col lg:space-y-0 lg:gap-3 lg:overflow-hidden">
          <section aria-label="노드 목록" className="space-y-2 lg:max-h-[38%] lg:shrink-0 lg:overflow-y-auto">
          <h2 className="hmi-label">노드 목록</h2>
          {readOnly ? (
            <ul className="space-y-1 text-sm">
              {shownGraph.nodes.map((n) => (
                <li key={n.id}><button type="button" className="w-full break-words text-left hover:underline" onClick={() => setSelectedId(n.id)}><span className="hmi-code text-muted-foreground">{n.code}</span> {n.name || kindLabel(n.kind)}</button></li>
              ))}
            </ul>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">눌러서 더하거나 캔버스로 끌어다 놓습니다.</p>
              <ul className="grid grid-cols-2 gap-1">
                {KINDS.map((k) => (
                  <li key={k.kind}>
                    <button
                      type="button"
                      draggable
                      onDragStart={(e) => { e.dataTransfer.setData('application/x-flow-kind', k.kind); e.dataTransfer.effectAllowed = 'copy' }}
                      onClick={() => addNode(k.kind)}
                      title={k.hint}
                      className="w-full truncate border border-line-soft bg-raised px-2 py-1.5 text-left text-xs hover:bg-accent"
                    >
                      {k.label}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
          </section>
          <div className="border-t border-line-soft pt-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
            {versionsBody}
          </div>
        </aside>

        <div className="relative h-[420px] min-w-0 overflow-hidden border border-line-soft bg-background lg:h-auto lg:min-h-0" data-testid="flow-canvas">
          {viewingBar}
          {canvas}
        </div>

        <aside className="min-w-0 space-y-3 border border-line-soft bg-surface p-3 lg:min-h-0 lg:overflow-y-auto" aria-label="노드 설정">
          <div role="tablist" className="flex border-b border-line-soft">
            {([['test', '시험 대화'], ['log', '판단 기록'], ['config', '노드 설정']] as const).filter(([k]) => k === 'config' || backend.testChat).map(([k, label]) => (
              <button
                key={k} type="button" role="tab" aria-selected={rightTab === k} onClick={() => setRightTab(k)}
                className={`px-3 py-1.5 text-sm ${rightTab === k ? 'border-b-2 border-ink font-semibold text-ink' : 'text-muted-foreground'}`}
              >{label}</button>
            ))}
          </div>
          {panelBody}
        </aside>
      </div>
      )}
    </div>
  )
}

const SHEET_LABEL = { config: '노드 설정', test: '시험 대화', log: '판단 기록', versions: '버전' } as const

function Overview({ errors, graph, onPick }: { errors: FlowError[]; graph: FlowGraph; onPick: (id: string) => void }) {
  return (
    <div className="space-y-2">
      <h2 className="hmi-label">검증</h2>
      {errors.length === 0 ? (
        <p className="text-sm text-muted-foreground">오류가 없습니다. 노드를 눌러 설정을 봅니다.</p>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {errors.map((e, i) => {
            const n = graph.nodes.find((x) => x.id === e.node_id)
            return (
              <li key={i} className="border-l-2 border-alarm pl-2">
                {n ? <button type="button" className="hmi-code font-semibold underline" onClick={() => onPick(n.id)}>{n.code}</button> : <span className="hmi-code font-semibold">흐름</span>}{' '}
                <span className="hmi-code text-muted-foreground">{e.code}</span>
                <p className="text-muted-foreground">{e.message}</p>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** 노드 하나의 설정: 이름 + 「폼」(종류별) / 「고급」(JSON) 탭. 저장은 어느 쪽이든 config 통째 교체. */
function NodePanel({ node, schema, errors, readOnly, canDelete, commit, onDeleted }: {
  node: FlowNodeJson; schema?: object; errors: FlowError[]; readOnly: boolean; canDelete: boolean; commit: (ops: PatchOp[]) => void; onDeleted: () => void
}) {
  const pretty = JSON.stringify(node.config ?? {}, null, 2)
  const [text, setText] = useState(pretty)
  const [nameDraft, setNameDraft] = useState(node.name ?? '')
  const [problem, setProblem] = useState('')
  const [tab, setTab] = useState<'form' | 'json'>('form')
  const { byPath, general } = fieldErrors(errors.filter((e) => e.node_id === node.id))
  const showForm = tab === 'form' && schema !== undefined

  useEffect(() => setText(pretty), [pretty])

  const apply = () => {
    let parsed: unknown
    try { parsed = JSON.parse(text) } catch (e) { setProblem(`JSON 이 올바르지 않습니다: ${(e as Error).message}`); return }
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) { setProblem('JSON 이 올바르지 않습니다: 객체({ … })여야 합니다.'); return }
    setProblem('')
    commit([{ op: 'upsert_node', id: node.id, config: parsed as Record<string, unknown> }])
  }

  const tabButton = (id: 'form' | 'json', label: string) => (
    <button
      type="button" role="tab" aria-selected={(id === 'form') === showForm} onClick={() => setTab(id)}
      className={`border-b-2 px-3 py-1 text-sm ${(id === 'form') === showForm ? 'border-ink font-semibold text-ink' : 'border-transparent text-muted-foreground hover:text-ink'}`}
    >
      {label}
    </button>
  )

  return (
    <div className="space-y-3">
      <div>
        <p className="hmi-code text-muted-foreground">{node.code} · {kindLabel(node.kind)}</p>
        <h2 className="font-cond text-base font-semibold text-ink">{node.name || kindLabel(node.kind)}</h2>
      </div>
      {general.length > 0 && (
        <div role="alert" className="space-y-1 text-sm">
          {general.map((e, i) => <p key={i} className="border-l-2 border-alarm pl-2"><span className="hmi-code">{e.code}</span> <span className="text-muted-foreground">{e.message}</span></p>)}
        </div>
      )}
      <div className="space-y-1">
        <Label htmlFor="node-name">이름</Label>
        <Input
          id="node-name"
          value={nameDraft}
          readOnly={readOnly}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={() => { if (!readOnly && nameDraft !== (node.name ?? '')) commit([{ op: 'upsert_node', id: node.id, name: nameDraft }]) }}
        />
      </div>
      {schema !== undefined && <div role="tablist" className="flex border-b border-line-soft">{tabButton('form', '폼')}{tabButton('json', '고급')}</div>}
      {showForm ? (
        <NodeConfigForm
          kind={node.kind} schema={schema} config={node.config ?? {}} readOnly={readOnly} errors={byPath}
          onChange={(next) => commit([{ op: 'upsert_node', id: node.id, config: next }])}
        />
      ) : (
        <div className="space-y-1">
          <Label htmlFor="node-config">설정(JSON)</Label>
          <Textarea id="node-config" className="hmi-code min-h-[180px] w-full" value={text} readOnly={readOnly} onChange={(e) => setText(e.target.value)} spellCheck={false} />
          {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
          {!readOnly && <Button type="button" size="sm" onClick={apply}>설정 적용</Button>}
        </div>
      )}
      {!readOnly && canDelete && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => { commit([{ op: 'delete_node', id: node.id }]); onDeleted() }}><Trash2 className="h-4 w-4" />노드 삭제</Button>
        </div>
      )}
    </div>
  )
}
