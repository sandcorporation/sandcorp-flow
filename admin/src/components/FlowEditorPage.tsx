import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Trash2 } from 'lucide-react'
import { currentAgentRole } from '../api'
import { appsFlowsApiFlowsGet, appsFlowsApiFlowsPatchDraft } from '../generated/endpoints/flows/flows'
import type { DraftPatchIn, FlowDetailOut } from '../generated/model'
import { HttpError } from '../mutator'
import {
  KINDS, applyOps, flowLevelErrors, kindLabel, newNode,
  type FlowError, type FlowGraph, type FlowNodeJson, type PatchOp,
} from '@/lib/flowGraph'
import FlowCanvas from './FlowCanvas'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Textarea } from './ui/textarea'

type SaveState = 'saved' | 'saving' | 'failed'
const EMPTY: FlowGraph = { nodes: [], edges: [] }

/** 편집기: 왼쪽 노드 목록 · 가운데 캔버스 · 오른쪽 패널. 모든 편집은 패치 연산으로 곧바로 초안에 저장한다. */
export default function FlowEditorPage() {
  const { id = '' } = useParams()
  const readOnly = currentAgentRole() !== 'admin' // flow.edit 는 Admin 전용(백엔드 permissions.py 와 같은 판정)
  const [name, setName] = useState('')
  const [graph, setGraph] = useState<FlowGraph>(EMPTY)
  const [errors, setErrors] = useState<FlowError[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [save, setSave] = useState<{ state: SaveState; message: string }>({ state: 'saved', message: '' })
  const graphRef = useRef(graph)
  graphRef.current = graph
  const queue = useRef<Promise<void>>(Promise.resolve())

  const load = useCallback(async () => {
    const r = await appsFlowsApiFlowsGet(id)
    const d = r.data as unknown as FlowDetailOut
    setName(d.flow.name)
    const raw = d.draft.graph as unknown as Partial<FlowGraph>
    setGraph({ nodes: raw.nodes ?? [], edges: raw.edges ?? [] })
    setErrors(d.draft.errors as FlowError[])
  }, [id])

  useEffect(() => {
    load().then(() => setLoaded(true)).catch(() => setLoadError('흐름을 불러오지 못했습니다.'))
  }, [load])

  /** 화면에 먼저 반영하고, 연산을 순서대로 하나씩 보낸다. 거절되면 서버 초안으로 다시 맞춘다. */
  const commit = useCallback((ops: PatchOp[]) => {
    if (readOnly || ops.length === 0) return
    setGraph((g) => applyOps(g, ops))
    setSave({ state: 'saving', message: '' })
    queue.current = queue.current.then(async () => {
      try {
        const r = await appsFlowsApiFlowsPatchDraft(id, { ops } as unknown as DraftPatchIn)
        setErrors(r.data.errors as FlowError[])
        setSave({ state: 'saved', message: '' })
      } catch (err) {
        const body = err instanceof HttpError ? (err.body as { detail?: string } | undefined) : undefined
        await load().catch(() => {})
        setSave({ state: 'failed', message: `저장하지 못했습니다${body?.detail ? `: ${body.detail}` : '.'} 서버의 초안으로 다시 맞췄습니다.` })
      }
    })
  }, [id, load, readOnly])

  const addNode = (kind: string, at?: { x: number; y: number }) => {
    const g = graphRef.current
    const slot = g.nodes.length
    const position = at ?? { x: 40 + (slot % 4) * 240, y: 40 + Math.floor(slot / 4) * 150 }
    const n = newNode(g, kind, position)
    commit([{ op: 'upsert_node', id: n.id, kind: n.kind, code: n.code, name: n.name, position: n.position, config: n.config }])
    setSelectedId(n.id)
  }

  const selected = graph.nodes.find((n) => n.id === selectedId) ?? null
  const flowErrors = flowLevelErrors(errors)

  if (loadError) return <p role="alert" className="text-sm text-destructive">{loadError}</p>
  if (!loaded) return <div aria-busy="true" />

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link to="/tenant/flows" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-ink"><ArrowLeft className="h-4 w-4" />흐름 목록</Link>
        <h1 className="font-cond text-lg font-semibold tracking-wide text-ink">{name}</h1>
        <span className="hmi-label flex items-center gap-1.5" role="status">
          <span className={`lamp ${save.state === 'saving' ? 'lamp-run' : save.state === 'failed' ? 'lamp-alarm' : 'lamp-ok'}`} />
          {save.state === 'saving' ? '저장 중' : save.state === 'failed' ? '저장 실패' : '초안 저장됨'}
        </span>
        <span className="hmi-label">{errors.length === 0 ? '검증 통과' : `검증 오류 ${errors.length}건`}</span>
        {readOnly && <span className="hmi-label border border-line-soft px-1.5 py-0.5">읽기 전용 — 흐름 편집 권한이 없습니다</span>}
      </div>
      {flowErrors.map((e, i) => <p key={i} role="alert" className="text-sm text-destructive">{e.message}</p>)}
      {save.state === 'failed' && <p role="alert" className="text-sm text-destructive">{save.message}</p>}

      <div className="grid gap-3 lg:grid-cols-[190px_minmax(0,1fr)_300px]">
        <aside className="space-y-2 border border-line-soft bg-surface p-3">
          <h2 className="hmi-label">노드 목록</h2>
          {readOnly ? (
            <ul className="space-y-1 text-sm">
              {graph.nodes.map((n) => (
                <li key={n.id}><button type="button" className="w-full truncate text-left hover:underline" onClick={() => setSelectedId(n.id)}><span className="hmi-code text-muted-foreground">{n.code}</span> {n.name || kindLabel(n.kind)}</button></li>
              ))}
            </ul>
          ) : (
            <>
              <p className="text-xs text-muted-foreground">눌러서 더하거나 캔버스로 끌어다 놓습니다.</p>
              <ul className="grid grid-cols-2 gap-1 lg:grid-cols-1">
                {KINDS.map((k) => (
                  <li key={k.kind}>
                    <button
                      type="button"
                      draggable
                      onDragStart={(e) => { e.dataTransfer.setData('application/x-flow-kind', k.kind); e.dataTransfer.effectAllowed = 'copy' }}
                      onClick={() => addNode(k.kind)}
                      title={k.hint}
                      className="w-full border border-line-soft bg-raised px-2 py-1.5 text-left text-sm hover:bg-accent"
                    >
                      {k.label}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </aside>

        <div className="relative h-[420px] min-w-0 overflow-hidden border border-line-soft bg-background lg:h-[620px]" data-testid="flow-canvas">
          <FlowCanvas
            graph={graph}
            errors={errors}
            selectedId={selectedId}
            readOnly={readOnly}
            onSelect={setSelectedId}
            onMoved={(nid, position) => commit([{ op: 'upsert_node', id: nid, position }])}
            onConnect={(from, handle, to) => commit([{ op: 'connect', from, to, handle }])}
            onDelete={(ids, cuts) => {
              commit([...ids.map((x): PatchOp => ({ op: 'delete_node', id: x })), ...cuts.map((c): PatchOp => ({ op: 'disconnect', from: c.from, handle: c.handle }))])
              if (selectedId && ids.includes(selectedId)) setSelectedId(null)
            }}
            onDropKind={(kind, position) => addNode(kind, { x: Math.round(position.x), y: Math.round(position.y) })}
          />
        </div>

        <aside className="min-w-0 space-y-3 border border-line-soft bg-surface p-3" aria-label="노드 설정">
          {selected ? (
            <NodePanel key={selected.id} node={selected} errors={errors} readOnly={readOnly} commit={commit} onDeleted={() => setSelectedId(null)} />
          ) : (
            <Overview errors={errors} graph={graph} onPick={setSelectedId} />
          )}
        </aside>
      </div>
    </div>
  )
}

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

/** 종류별 폼은 #98 이 이 자리를 채운다. 지금은 모든 종류에 쓰는 범용 JSON 편집. */
function NodePanel({ node, errors, readOnly, commit, onDeleted }: {
  node: FlowNodeJson; errors: FlowError[]; readOnly: boolean; commit: (ops: PatchOp[]) => void; onDeleted: () => void
}) {
  const pretty = JSON.stringify(node.config ?? {}, null, 2)
  const [text, setText] = useState(pretty)
  const [nameDraft, setNameDraft] = useState(node.name ?? '')
  const [problem, setProblem] = useState('')
  const mine = errors.filter((e) => e.node_id === node.id)

  useEffect(() => setText(pretty), [pretty])

  const apply = () => {
    let parsed: unknown
    try { parsed = JSON.parse(text) } catch (e) { setProblem(`JSON 이 올바르지 않습니다: ${(e as Error).message}`); return }
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) { setProblem('JSON 이 올바르지 않습니다: 객체({ … })여야 합니다.'); return }
    setProblem('')
    commit([{ op: 'upsert_node', id: node.id, config: parsed as Record<string, unknown> }])
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="hmi-code text-muted-foreground">{node.code} · {kindLabel(node.kind)}</p>
        <h2 className="font-cond text-base font-semibold text-ink">{node.name || kindLabel(node.kind)}</h2>
      </div>
      {mine.length > 0 && (
        <ul className="space-y-1 text-sm">
          {mine.map((e, i) => <li key={i} className="border-l-2 border-alarm pl-2"><span className="hmi-code">{e.code}</span><p className="text-muted-foreground">{e.message}</p></li>)}
        </ul>
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
      <div className="space-y-1">
        <Label htmlFor="node-config">설정(JSON)</Label>
        <Textarea id="node-config" className="hmi-code min-h-[180px] w-full" value={text} readOnly={readOnly} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
      </div>
      {!readOnly && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" onClick={apply}>설정 적용</Button>
          <Button type="button" size="sm" variant="outline" onClick={() => { commit([{ op: 'delete_node', id: node.id }]); onDeleted() }}><Trash2 className="h-4 w-4" />노드 삭제</Button>
        </div>
      )}
    </div>
  )
}
