import React, { useState, useEffect, useRef, ChangeEvent } from 'react'
import { listDocuments, uploadDocument, updateDocument, deleteDocument, listDocumentChunks, getGraphStatus, rebuildGraph } from '../api'
import type { DocumentOut, ChunkOut } from '../generated/model'
import { Button, buttonVariants } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { cn } from '@/lib/utils'

const STATUS_VARIANT: Record<string, 'default' | 'secondary' | 'success' | 'destructive'> = {
  pending: 'secondary', processing: 'default', ready: 'success', failed: 'destructive',
}
const FRESHNESS_LABEL: Record<string, string> = { fresh: '최신', stale: '재구축 필요', rebuilding: '재구축 중…' }
const FRESHNESS_VARIANT: Record<string, 'success' | 'secondary' | 'default'> = { fresh: 'success', stale: 'secondary', rebuilding: 'default' }

type ChunkState = ChunkOut[] | 'loading' | undefined

export default function DocumentsTab() {
  const [docs, setDocs] = useState<DocumentOut[]>([])
  const [uploading, setUploading] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [uploadLabel, setUploadLabel] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [expandedChunks, setExpandedChunks] = useState<Record<string, ChunkState>>({})
  const [graphFreshness, setGraphFreshness] = useState<string | null>(null)

  const loadGraphStatus = async () => {
    try {
      const data = await getGraphStatus()
      setGraphFreshness(data.freshness)
    } catch { /* ignore */ }
  }

  const handleRebuildGraph = async () => {
    await rebuildGraph()
    setGraphFreshness('rebuilding')
    setTimeout(loadGraphStatus, 1500)
  }

  const load = async () => setDocs(await listDocuments())

  useEffect(() => {
    load()
    loadGraphStatus()
    const interval = setInterval(() => { load(); loadGraphStatus() }, 3000)
    return () => clearInterval(interval)
  }, [])

  const handleFileSelect = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setPendingFile(file)
    setUploadLabel(file.name)
  }

  const cancelUpload = () => {
    setPendingFile(null)
    setUploadLabel('')
    if (fileRef.current) fileRef.current.value = ''
  }

  const confirmUpload = async () => {
    if (!pendingFile) return
    setUploading(true)
    await uploadDocument(pendingFile, uploadLabel.trim())
    await load()
    setUploading(false)
    cancelUpload()
  }

  const startEdit = (doc: DocumentOut) => {
    setEditingId(doc.id)
    setEditLabel(doc.name)
  }

  const saveEdit = async (id: string) => {
    const name = editLabel.trim()
    if (!name) return
    await updateDocument(id, name)
    setEditingId(null)
    setEditLabel('')
    await load()
  }

  const handleDelete = async (id: string) => {
    if (!confirm('삭제하시겠습니까?')) return
    await deleteDocument(id)
    setExpandedChunks(prev => { const n = { ...prev }; delete n[id]; return n })
    await load()
  }

  const toggleChunks = async (docId: string) => {
    if (expandedChunks[docId] !== undefined) {
      setExpandedChunks(prev => { const n = { ...prev }; delete n[docId]; return n })
      return
    }
    setExpandedChunks(prev => ({ ...prev, [docId]: 'loading' }))
    const chunks = await listDocumentChunks(docId)
    setExpandedChunks(prev => ({ ...prev, [docId]: chunks }))
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <input ref={fileRef} type="file" accept=".pdf,.txt,.png,.jpg,.jpeg,.webp"
          onChange={handleFileSelect} className="hidden" id="file-upload" />
        <label htmlFor="file-upload" className={cn(buttonVariants({ variant: 'default' }), 'cursor-pointer')}>
          {uploading ? '업로드 중...' : '📄 문서 업로드 (PDF/TXT/이미지)'}
        </label>
        <span className="text-xs text-muted-foreground">3초마다 상태 자동 갱신</span>
        {graphFreshness && (
          <span className="ml-auto flex items-center gap-2" data-testid="graph-freshness">
            <span className="text-xs text-muted-foreground">지식그래프</span>
            <Badge variant={FRESHNESS_VARIANT[graphFreshness] || 'secondary'}>{FRESHNESS_LABEL[graphFreshness] || graphFreshness}</Badge>
            <Button size="sm" variant="outline" data-testid="rebuild-graph" onClick={handleRebuildGraph} disabled={graphFreshness === 'rebuilding'}>재구축</Button>
          </span>
        )}
      </div>

      {pendingFile && (
        <Card className="border-primary/30" data-testid="upload-modal">
          <CardContent className="space-y-2 pt-5">
            <Label>Document Label (제품명·모델명으로 지정하면 검색이 정확해집니다)</Label>
            <div className="flex items-center gap-2">
              <Input data-testid="upload-label-input" className="flex-1" value={uploadLabel}
                onChange={e => setUploadLabel(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && confirmUpload()} />
              <Button data-testid="upload-confirm" onClick={confirmUpload} disabled={uploading || !uploadLabel.trim()}>
                {uploading ? '업로드 중...' : '업로드'}
              </Button>
              <Button variant="destructive" onClick={cancelUpload} disabled={uploading}>취소</Button>
            </div>
            <div className="text-xs text-muted-foreground">{pendingFile.name}</div>
          </CardContent>
        </Card>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>파일명</TableHead>
            <TableHead>상태</TableHead>
            <TableHead>작업</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {docs.map(d => {
            const chunkState = expandedChunks[d.id]
            return (
              <React.Fragment key={d.id}>
                <TableRow>
                  <TableCell>
                    {editingId === d.id ? (
                      <span className="flex items-center gap-2">
                        <Input data-testid="edit-label-input" className="h-8 text-sm" value={editLabel}
                          onChange={e => setEditLabel(e.target.value)}
                          onKeyDown={e => e.key === 'Enter' && saveEdit(d.id)} autoFocus />
                        <Button size="sm" data-testid="save-label" onClick={() => saveEdit(d.id)}>저장</Button>
                        <Button size="sm" variant="destructive" onClick={() => { setEditingId(null); setEditLabel('') }}>취소</Button>
                      </span>
                    ) : (
                      <span className="flex items-center gap-2">
                        {d.name}
                        <button data-testid="edit-label" title="레이블 수정"
                          className="cursor-pointer text-xs text-foreground underline decoration-primary decoration-2 underline-offset-2" onClick={() => startEdit(d)}>✏️ 레이블 수정</button>
                      </span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANT[d.status] || 'secondary'}>{d.status}</Badge>
                    {d.error_message && <span className="ml-2 text-xs text-destructive">{d.error_message}</span>}
                  </TableCell>
                  <TableCell>
                    <Button size="sm" variant="secondary" className="mr-2" onClick={() => toggleChunks(d.id)} data-testid={`chunks-toggle-${d.id}`}>
                      {chunkState !== undefined ? '청크 닫기' : '청크 보기'}
                    </Button>
                    <Button size="sm" variant="destructive" onClick={() => handleDelete(d.id)}>삭제</Button>
                  </TableCell>
                </TableRow>
                {chunkState !== undefined && (
                  <TableRow>
                    <TableCell colSpan={3} className="bg-muted/40 p-0">
                      <div className="p-3" data-testid={`chunks-panel-${d.id}`}>
                        {chunkState === 'loading' && <div className="text-sm text-muted-foreground">청크 로딩 중...</div>}
                        {Array.isArray(chunkState) && chunkState.length === 0 && <div className="text-sm text-muted-foreground">청크 없음</div>}
                        {Array.isArray(chunkState) && chunkState.map(c => (
                          <div key={c.chunk_index} data-testid="chunk-item" className="mb-2 border-b border-border pb-2 text-xs">
                            <span className="mr-2 font-bold font-mono text-ink">#{c.chunk_index}</span>
                            <span data-testid="chunk-content" className="text-muted-foreground">{c.content.slice(0, 200)}{c.content.length > 200 ? '…' : ''}</span>
                          </div>
                        ))}
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </React.Fragment>
            )
          })}
          {docs.length === 0 && (
            <TableRow><TableCell colSpan={3} className="text-center text-muted-foreground">문서가 없습니다</TableCell></TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  )
}
