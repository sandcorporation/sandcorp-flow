import { useEffect, useState } from 'react'
import { listFlowOptions } from '../api'

/** 흐름 필터 — 값이 ''이면 전체. 흐름이 하나뿐이면 그릴 것이 없어 숨긴다. */
export default function FlowFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [flows, setFlows] = useState<{ id: string; name: string }[]>([])
  useEffect(() => {
    let alive = true
    Promise.resolve(listFlowOptions()).then(f => { if (alive) setFlows(Array.isArray(f) ? f : []) }).catch(() => {})
    return () => { alive = false }
  }, [])
  if (flows.length < 2 && !value) return null
  return (
    <select aria-label="흐름" value={value} onChange={e => onChange(e.target.value)}
      className="h-8 max-w-[10rem] rounded-md border border-input bg-background px-2 text-xs">
      <option value="">모든 흐름</option>
      {flows.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
    </select>
  )
}
