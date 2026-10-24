import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, applyNodeChanges,
  useReactFlow, type Connection, type Edge, type Node, type NodeChange, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  DEFAULT_HANDLE, edgeKey, errorsByNode, kindLabel, nodeStyle, nodeSummary, outHandles, pathTo,
  type FlowError, type FlowGraph, type FlowNodeJson,
} from '@/lib/flowGraph'

/** 카드 한 장. 가드레일=황·흑 경고띠 머리, 사람 넘김=적색 점선 테두리(시안 .node.guard / .node.human). */
type CardData = { node: FlowNodeJson; errors: FlowError[]; active: boolean; selected: boolean; readOnly: boolean }

function FlowNodeCard({ data }: NodeProps<Node<CardData>>) {
  const { node, errors, active, selected, readOnly } = data
  const style = nodeStyle(node.kind)
  const handles = outHandles(node)
  const bar = style === 'guard' ? 'hazard-stripe text-[#121412]' : style === 'human' ? 'bg-alarm text-white' : 'bg-head text-head-foreground'
  const box = [
    'relative w-[180px] overflow-visible border bg-raised text-left',
    style === 'human' ? 'border-dashed border-alarm' : 'border-ink',
    active ? 'outline outline-[3px] outline-run' : selected ? 'outline outline-2 outline-offset-2 outline-ink' : '',
  ].join(' ')
  return (
    <div className={box} data-node-id={node.id} data-style={style} style={{ minHeight: 78 + Math.max(0, handles.length - 1) * 16 }}>
      {node.kind !== 'start' && <Handle type="target" position={Position.Left} isConnectable={!readOnly} />}
      <div className={`flex items-center gap-1.5 px-2 py-1 text-[0.7rem] font-medium ${bar}`}>
        <span className={style === 'guard' ? 'flex items-center gap-1.5 bg-hazard px-1' : 'flex items-center gap-1.5'}>
          <span className={`lamp ${active ? 'lamp-run' : errors.length ? 'lamp-alarm' : 'lamp-ok'}`} />
          <span className="hmi-code opacity-90">{node.code}</span>
          <span>{kindLabel(node.kind)}</span>
        </span>
        {errors.length > 0 && (
          <span
            role="img"
            aria-label={`${node.code} 오류 ${errors.length}건`}
            title={errors.map((e) => `${e.code}: ${e.message}`).join('\n')}
            className="ml-auto bg-alarm px-1 font-mono text-[0.66rem] text-white"
          >
            ! {errors.length}
          </span>
        )}
      </div>
      <h5 className="mx-2 mt-1.5 truncate text-[0.88rem] font-semibold text-ink">{node.name || kindLabel(node.kind)}</h5>
      <p className="mx-2 mt-0.5 mb-2 line-clamp-2 break-all text-[0.72rem] text-muted-foreground">{nodeSummary(node)}</p>
      {handles.map((h, i) => {
        const top = handles.length === 1 ? '50%' : `${((i + 1) / (handles.length + 1)) * 100}%`
        return (
          <div key={h}>
            <Handle id={h} type="source" position={Position.Right} style={{ top }} isConnectable={!readOnly} />
            {handles.length > 1 && (
              <span className="hmi-code pointer-events-none absolute right-2 -translate-y-1/2 bg-raised px-0.5 text-muted-foreground" style={{ top }}>{h}</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

const nodeTypes = { flow: FlowNodeCard }

export type FlowCanvasProps = {
  graph: FlowGraph
  errors: FlowError[]
  selectedId: string | null
  readOnly: boolean
  /** 호박색으로 강조할 노드·선(시험 대화의 실행 경로 등, #30). 없으면 고른 노드까지 오는 길을 강조한다. */
  activeNodeIds?: ReadonlySet<string>
  activeEdgeKeys?: ReadonlySet<string>
  onSelect: (id: string | null) => void
  onMoved: (id: string, position: { x: number; y: number }) => void
  onConnect: (from: string, handle: string, to: string) => void
  onDelete: (nodeIds: string[], edgesToCut: { from: string; handle: string }[]) => void
  onDropKind: (kind: string, position: { x: number; y: number }) => void
}

function Inner(p: FlowCanvasProps) {
  const { graph, errors, selectedId, readOnly } = p
  const rf = useReactFlow()
  const [rfNodes, setRfNodes] = useState<Node<CardData>[]>([])
  const byNode = useMemo(() => errorsByNode(errors), [errors])
  const path = useMemo(() => (selectedId ? pathTo(graph, selectedId) : { nodes: new Set<string>(), edges: new Set<string>() }), [graph, selectedId])
  const activeNodes = p.activeNodeIds ?? path.nodes
  const activeEdges = p.activeEdgeKeys ?? path.edges

  // 그래프(저장된 모양)가 바뀌면 카드 데이터를 새로 만들되, 측정값·끌고 있는 위치는 이어 간다.
  useEffect(() => {
    setRfNodes((prev) =>
      graph.nodes.map((n) => {
        const old = prev.find((x) => x.id === n.id)
        return {
          ...(old ?? {}),
          id: n.id,
          type: 'flow',
          position: old?.dragging ? old.position : n.position,
          draggable: !readOnly,
          selected: n.id === selectedId,
          data: { node: n, errors: byNode.get(n.id) ?? [], active: activeNodes.has(n.id) && p.activeNodeIds !== undefined, selected: n.id === selectedId, readOnly },
        }
      }),
    )
  }, [graph, byNode, selectedId, readOnly, activeNodes, p.activeNodeIds])

  const edges: Edge[] = useMemo(
    () =>
      graph.edges.map((e) => {
        const handle = e.handle || DEFAULT_HANDLE
        const to = graph.nodes.find((n) => n.id === e.to)
        const human = to?.kind === 'handoff' || handle === 'handoff'
        const on = activeEdges.has(edgeKey(e))
        const color = on ? 'hsl(var(--flow-run))' : human ? 'hsl(var(--flow-alarm))' : 'hsl(var(--flow-muted))'
        return {
          id: edgeKey(e),
          source: e.from,
          sourceHandle: handle,
          target: e.to,
          label: handle === DEFAULT_HANDLE ? undefined : handle,
          labelStyle: { fontFamily: 'var(--flow-font-mono)', fontSize: 10.5, fill: human ? 'hsl(var(--flow-alarm))' : 'hsl(var(--flow-text))' },
          labelBgStyle: { fill: 'hsl(var(--flow-bg))' },
          style: { stroke: color, strokeWidth: on ? 3 : 1.5, strokeDasharray: human ? '7 4' : undefined },
          markerEnd: { type: MarkerType.ArrowClosed, color },
          deletable: !readOnly,
        }
      }),
    [graph, activeEdges, readOnly],
  )

  const onNodesChange = useCallback((changes: NodeChange<Node<CardData>>[]) => setRfNodes((ns) => applyNodeChanges(changes, ns)), [])

  const onConnect = (c: Connection) => {
    if (readOnly || !c.source || !c.target || c.source === c.target) return
    p.onConnect(c.source, c.sourceHandle || DEFAULT_HANDLE, c.target)
  }

  return (
    <div
      className="h-full w-full"
      onDragOver={(e) => { if (!readOnly) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' } }}
      onDrop={(e) => {
        const kind = e.dataTransfer.getData('application/x-flow-kind')
        if (readOnly || !kind) return
        e.preventDefault()
        p.onDropKind(kind, rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }))
      }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(_, n) => p.onSelect(n.id)}
        onPaneClick={() => p.onSelect(null)}
        onNodeDragStop={(_, n) => p.onMoved(n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) })}
        onConnect={onConnect}
        onDelete={({ nodes, edges: gone }) => {
          const ids = new Set(nodes.map((n) => n.id))
          const cuts = gone.filter((e) => !ids.has(e.source) && !ids.has(e.target)).map((e) => ({ from: e.source, handle: e.sourceHandle || DEFAULT_HANDLE }))
          p.onDelete([...ids], cuts)
        }}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable
        deleteKeyCode={readOnly ? null : ['Backspace', 'Delete']}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        minZoom={0.3}
        colorMode="system"
      >
        <Background variant={BackgroundVariant.Lines} gap={24} color="hsl(var(--flow-grid))" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}

export default function FlowCanvas(props: FlowCanvasProps) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  )
}
