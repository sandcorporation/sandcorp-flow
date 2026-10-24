import { useState } from 'react'
import { listMemories, updateMemory, deleteMemory } from '../api'
import { s } from '../styles'
import type { MemoryOut } from '../generated/model'

export default function MemoryTab() {
  const [visitorId, setVisitorId] = useState('')
  const [memories, setMemories] = useState<MemoryOut[]>([])
  const [searched, setSearched] = useState(false)
  const [editing, setEditing] = useState<MemoryOut | null>(null)

  const search = async () => {
    if (!visitorId.trim()) return
    const data = await listMemories(visitorId.trim())
    setMemories(data)
    setSearched(true)
  }

  const handleDelete = async (memId: string) => {
    await deleteMemory(visitorId, memId)
    setMemories(m => m.filter(x => x.id !== memId))
  }

  const handleUpdate = async (mem: MemoryOut) => {
    const updated = await updateMemory(visitorId, mem.id, { key: mem.key, value: mem.value })
    setMemories(m => m.map(x => x.id === mem.id ? updated : x))
    setEditing(null)
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
        <input
          style={{ ...s.input, flex: 1 }}
          placeholder="Visitor ID 검색"
          value={visitorId}
          onChange={e => setVisitorId(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && search()}
        />
        <button style={s.btn} onClick={search}>검색</button>
      </div>

      {searched && (
        memories.length === 0
          ? <p style={{ color: 'hsl(var(--muted-foreground))' }}>Memory가 없습니다.</p>
          : (
            <table style={s.table}>
              <thead>
                <tr>
                  <th style={s.th}>Key</th>
                  <th style={s.th}>Value</th>
                  <th style={s.th}>작업</th>
                </tr>
              </thead>
              <tbody>
                {memories.map(m => (
                  <tr key={m.id}>
                    <td style={s.td}>
                      {editing?.id === m.id
                        ? <input style={s.input} value={editing.key} onChange={e => setEditing(x => x ? {...x, key: e.target.value} : x)} />
                        : m.key}
                    </td>
                    <td style={s.td}>
                      {editing?.id === m.id
                        ? <input style={s.input} value={editing.value} onChange={e => setEditing(x => x ? {...x, value: e.target.value} : x)} />
                        : m.value}
                    </td>
                    <td style={s.td}>
                      {editing?.id === m.id
                        ? <>
                            <button style={s.btnSm} onClick={() => handleUpdate(editing!)}>저장</button>
                            <button style={{ ...s.btnSm, marginLeft: 4 }} onClick={() => setEditing(null)}>취소</button>
                          </>
                        : <>
                            <button style={s.btnSm} onClick={() => setEditing({...m})}>수정</button>
                            <button style={{ ...s.btnDanger, marginLeft: 4 }} onClick={() => handleDelete(m.id)}>삭제</button>
                          </>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
      )}
    </div>
  )
}
