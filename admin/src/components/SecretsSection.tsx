import { useCallback, useEffect, useState } from 'react'
import {
  listSecrets, putSecret, deleteSecret, listAllowedDomains, addAllowedDomain, deleteAllowedDomain,
} from '../api'
import type { SecretItem, AllowedDomainItem } from '../api'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

const hint = 'mt-1 text-xs text-muted-foreground'
const errorCls = 'mt-1 text-sm text-destructive'
const rowCls = 'flex flex-wrap items-center gap-2 border-b border-border py-2 last:border-b-0'
const msg = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** 설정 > 「비밀·허용 도메인」. 값은 쓰기 전용이라 입력 직후 상태에서 비우고, 목록엔 이름만 온다. */
export default function SecretsSection({ isAdmin }: { isAdmin: boolean }) {
  return (
    <div className="space-y-8">
      <DomainsPart isAdmin={isAdmin} />
      <SecretsPart isAdmin={isAdmin} />
    </div>
  )
}

function DomainsPart({ isAdmin }: { isAdmin: boolean }) {
  const [items, setItems] = useState<AllowedDomainItem[]>([])
  const [pattern, setPattern] = useState('')
  const [error, setError] = useState('')
  const reload = useCallback(async () => {
    try { setItems(await listAllowedDomains()) } catch (e) { setError(msg(e)) }
  }, [])
  useEffect(() => { void reload() }, [reload])

  const add = async () => {
    setError('')
    try { await addAllowedDomain(pattern.trim()); setPattern(''); await reload() }
    catch (e) { setError(msg(e)) }
  }
  const remove = async (d: AllowedDomainItem) => {
    setError('')
    try { await deleteAllowedDomain(d.id); await reload() } catch (e) { setError(msg(e)) }
  }

  return (
    <section aria-label="허용 도메인" className="space-y-3">
      <div>
        <h3 className="text-sm font-semibold">허용 도메인</h3>
        <p className="text-xs text-muted-foreground">HTTP 도구가 호출해도 되는 외부 주소입니다. 목록에 없는 도메인은 호출하지 않습니다.</p>
      </div>
      {items.length === 0
        ? <p className={hint}>등록된 도메인이 없습니다.</p>
        : <ul>{items.map(d => (
            <li key={d.id} className={rowCls}>
              <code className="min-w-0 flex-1 break-all text-sm">{d.pattern}</code>
              {isAdmin && <Button size="sm" variant="outline" onClick={() => remove(d)} aria-label={`${d.pattern} 삭제`}>삭제</Button>}
            </li>
          ))}</ul>}
      {isAdmin ? (
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Input aria-label="허용 도메인 패턴" className="w-full sm:w-72" value={pattern} placeholder="api.example.com"
              onChange={e => setPattern(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') void add() }} />
            <Button size="sm" onClick={add} disabled={!pattern.trim()}>도메인 추가</Button>
          </div>
          <p className={hint}>정확한 도메인(api.example.com) 또는 와일드카드 *.example.com(example.com의 하위 도메인 전부)를 쓸 수 있습니다. IP 주소는 넣을 수 없습니다.</p>
        </div>
      ) : <p className={hint}>도메인 추가·삭제는 관리자만 할 수 있습니다.</p>}
      {error && <p role="alert" className={errorCls}>{error}</p>}
    </section>
  )
}

function SecretsPart({ isAdmin }: { isAdmin: boolean }) {
  const [items, setItems] = useState<SecretItem[]>([])
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [newValue, setNewValue] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)
  const reload = useCallback(async () => {
    try { setItems(await listSecrets()) } catch (e) { setError(msg(e)) }
  }, [])
  useEffect(() => { void reload() }, [reload])

  const save = async () => {
    setError('')
    const v = value
    setValue('')  // 값은 보내는 즉시 상태에서 비운다(성공·실패 무관)
    try { await putSecret(name.trim(), v); setName(''); await reload() }
    catch (e) { setError(msg(e)) }
  }
  const replace = async (n: string) => {
    setError('')
    const v = newValue
    setNewValue('')
    try { await putSecret(n, v); setEditing(null); await reload() }
    catch (e) { setError(msg(e)) }
  }
  const remove = async (n: string) => {
    setError('')
    try { await deleteSecret(n); setConfirming(null); await reload() }
    catch (e) { setError(msg(e)); setConfirming(null) }
  }

  return (
    <section aria-label="비밀" className="space-y-3 border-t border-border pt-6">
      <div>
        <h3 className="text-sm font-semibold">비밀</h3>
        <p className="text-xs text-muted-foreground">API 키·토큰 같은 값을 이름으로 저장합니다. 흐름에서는 <code>{'{{secret.이름}}'}</code> 으로 쓰고, 저장한 값은 다시 볼 수 없습니다.</p>
      </div>
      {items.length === 0
        ? <p className={hint}>등록된 비밀이 없습니다.</p>
        : <ul>{items.map(s => (
            <li key={s.name} className={rowCls}>
              <code className="min-w-0 flex-1 break-all text-sm">{s.name}</code>
              {isAdmin && editing !== s.name && confirming !== s.name && (
                <>
                  <Button size="sm" variant="outline" aria-label={`${s.name} 값 바꾸기`}
                    onClick={() => { setEditing(s.name); setNewValue(''); setError('') }}>값 바꾸기</Button>
                  <Button size="sm" variant="outline" aria-label={`${s.name} 삭제`} onClick={() => setConfirming(s.name)}>삭제</Button>
                </>
              )}
              {isAdmin && editing === s.name && (
                <div className="flex w-full flex-wrap items-center gap-2">
                  <Input type="password" autoComplete="new-password" aria-label={`${s.name} 새 값`} className="w-full sm:w-64"
                    value={newValue} onChange={e => setNewValue(e.target.value)} />
                  <Button size="sm" onClick={() => replace(s.name)} disabled={!newValue}>교체</Button>
                  <Button size="sm" variant="outline" onClick={() => { setEditing(null); setNewValue('') }}>취소</Button>
                </div>
              )}
              {isAdmin && confirming === s.name && (
                <div className="flex w-full flex-wrap items-center gap-2">
                  <span className="text-sm">{s.name} 을(를) 지울까요? 이 비밀을 쓰는 흐름은 오류가 납니다.</span>
                  <Button size="sm" variant="destructive" onClick={() => remove(s.name)}>삭제 확인</Button>
                  <Button size="sm" variant="outline" onClick={() => setConfirming(null)}>취소</Button>
                </div>
              )}
            </li>
          ))}</ul>}
      {isAdmin ? (
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Input aria-label="비밀 이름" className="w-full sm:w-48" value={name} placeholder="CRM_TOKEN"
              autoComplete="off" onChange={e => setName(e.target.value)} />
            <Input type="password" autoComplete="new-password" aria-label="비밀 값" className="w-full sm:w-64" value={value}
              placeholder="값 (8자 이상)" onChange={e => setValue(e.target.value)} />
            <Button size="sm" onClick={save} disabled={!name.trim() || !value}>비밀 저장</Button>
          </div>
          <p className={hint}>이름은 영문·숫자·밑줄(_), 첫 글자는 숫자가 아니어야 하며 64자까지입니다. 같은 이름으로 저장하면 값이 교체됩니다.</p>
        </div>
      ) : <p className={hint}>비밀 추가·교체·삭제는 관리자만 할 수 있습니다.</p>}
      {error && <p role="alert" className={errorCls}>{error}</p>}
    </section>
  )
}
