import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { createPr, getProject, proposeEdit } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { EditResponse, Project } from '../types'

const POLL_INTERVAL_MS = 2_000

// Итог запроса на реальный PR (Этап 3b). 'stale' — сервер регенерировал diff и он разошёлся
// с показанным (409): не ретраим вслепую, просим пользователя перегенерировать правку.
type PrOutcome =
  | { kind: 'success'; url: string }
  | { kind: 'stale' }
  | { kind: 'error'; message: string }

function EditPanel() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [instruction, setInstruction] = useState('')
  const [result, setResult] = useState<EditResponse | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Инструкция, по которой сгенерирован показанный result.diff — именно её (не текущее
  // содержимое textarea, которое пользователь мог уже начать менять) шлём в /pr.
  const [sentInstruction, setSentInstruction] = useState('')
  const [prSending, setPrSending] = useState(false)
  const [prOutcome, setPrOutcome] = useState<PrOutcome | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // Свежий activeId для сверки в асинхронном handleSubmit (защита от гонки при переключении).
  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  // Смена активного проекта → предыдущий предпросмотр диффа не переносим (контексты
  // проектов не смешиваем).
  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setResult(null)
      setError(null)
      setPrOutcome(null)
    })
  }, [])

  const loadProject = useCallback(async () => {
    if (!activeId) {
      setProject(null)
      return
    }
    try {
      const p = await getProject(activeId)
      if (mountedRef.current) setProject(p)
    } catch {
      if (mountedRef.current) setProject(null)
    }
  }, [activeId])

  useEffect(() => {
    loadProject()
  }, [loadProject])

  const notReady = project !== null && project.status !== 'ready'
  useEffect(() => {
    if (!notReady) return
    const timer = setInterval(loadProject, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [notReady, loadProject])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = instruction.trim()
    if (!trimmed || !activeId) return
    const sentId = activeId
    setSending(true)
    setError(null)
    try {
      const res = await proposeEdit(sentId, trimmed)
      // Игнорируем ответ, если проект переключили, пока запрос летел.
      if (mountedRef.current && activeIdRef.current === sentId) {
        setResult(res)
        setSentInstruction(trimmed)
        // Новый предпросмотр — предыдущий исход PR (успех/устарело/ошибка) больше не о нём.
        setPrOutcome(null)
        // Свежий can_edit к моменту показа кнопки PR (мог измениться токен, пока пользователь
        // работал в чате/поиске на другой панели).
        await loadProject()
      }
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === sentId) {
        setError(err instanceof Error ? err.message : 'не удалось предложить правку')
      }
    } finally {
      if (mountedRef.current) setSending(false)
    }
  }

  async function handleConfirmPr() {
    if (!activeId || !result || !result.ok || prSending) return
    const sentId = activeId
    setPrSending(true)
    try {
      const res = await createPr(sentId, { instruction: sentInstruction, expected_diff: result.diff })
      if (!mountedRef.current || activeIdRef.current !== sentId) return
      if (res.ok) {
        setPrOutcome({ kind: 'success', url: res.pr_url })
      } else if (res.status === 409) {
        setPrOutcome({ kind: 'stale' })
      } else {
        setPrOutcome({ kind: 'error', message: res.reason })
      }
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === sentId) {
        setPrOutcome({
          kind: 'error',
          message: err instanceof Error ? err.message : 'не удалось открыть PR',
        })
      }
    } finally {
      if (mountedRef.current) setPrSending(false)
    }
  }

  return (
    <section className="edit-panel">
      <h2>Правка кода</h2>

      {!activeId ? (
        <p className="edit-hint">выберите готовый проект в списке выше</p>
      ) : notReady ? (
        <p className="edit-hint">
          проект {project?.name ? `«${project.name}» ` : ''}ещё индексируется — дождитесь статуса «готов»
        </p>
      ) : (
        <>
          {sending && <p className="edit-typing">готовлю правку…</p>}
          {error && <p className="edit-error">{error}</p>}
          {result && (
            <EditResult
              result={result}
              canEdit={project?.can_edit ?? false}
              prSending={prSending}
              prOutcome={prOutcome}
              onConfirmPr={handleConfirmPr}
            />
          )}

          <form className="edit-form" onSubmit={handleSubmit}>
            <textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="опишите правку: что и где изменить"
              aria-label="описание правки"
              disabled={sending || prSending}
              rows={3}
              maxLength={2000}
            />
            {/* prSending тоже блокирует форму: пока летит /pr по показанному diff'у, новая
                генерация не должна подменить result/sentInstruction под ответом ещё в пути —
                иначе итог PR может «прилипнуть» не к тому предпросмотру (см. code-review). */}
            <button type="submit" disabled={sending || prSending || !instruction.trim()}>
              {sending ? 'отправка…' : 'Предложить правку'}
            </button>
          </form>
        </>
      )}
    </section>
  )
}

function EditResult({
  result,
  canEdit,
  prSending,
  prOutcome,
  onConfirmPr,
}: {
  result: EditResponse
  canEdit: boolean
  prSending: boolean
  prOutcome: PrOutcome | null
  onConfirmPr: () => void
}) {
  if (!result.ok) {
    return (
      <div className="edit-abstain">
        <p>не могу выполнить правку: {result.reason}</p>
      </div>
    )
  }

  return (
    <div className="edit-result">
      <p className="edit-summary">{result.summary}</p>

      <pre className="edit-diff">
        <code>
          {result.diff.split('\n').map((line, i) => (
            <div key={i} className={diffLineClass(line)}>
              {line}
            </div>
          ))}
        </code>
      </pre>

      {result.edits.length > 0 && (
        <ul className="edit-files">
          {result.edits.map((e, i) => (
            <li key={i}>
              <span className="edit-file-path">{e.file}</span> — {e.reason}
            </li>
          ))}
        </ul>
      )}

      {result.sources.length > 0 && (
        <ol className="chat-sources">
          {result.sources.map((s, i) => (
            <li key={i} className="chat-source">
              <div className="chat-source-citation">{s.citation}</div>
              <pre className="chat-source-quote">
                <code>{s.quote}</code>
              </pre>
            </li>
          ))}
        </ol>
      )}

      <PrControl canEdit={canEdit} prSending={prSending} prOutcome={prOutcome} onConfirmPr={onConfirmPr} />
    </div>
  )
}

// Реальный PR (Этап 3b). Кнопка активна только если у проекта включены правки (валидный
// per-project GitHub PAT). После success/stale — терминальное состояние: чтобы попробовать
// снова, нужно перегенерировать правку (новая кнопка «Предложить правку» очистит prOutcome).
function PrControl({
  canEdit,
  prSending,
  prOutcome,
  onConfirmPr,
}: {
  canEdit: boolean
  prSending: boolean
  prOutcome: PrOutcome | null
  onConfirmPr: () => void
}) {
  if (!canEdit) {
    return <p className="edit-hint edit-pr-hint">включите правки токеном проекта, чтобы открыть PR</p>
  }

  if (prOutcome?.kind === 'success') {
    return (
      <p className="edit-pr-success">
        PR открыт:{' '}
        <a href={prOutcome.url} target="_blank" rel="noopener noreferrer">
          {prOutcome.url}
        </a>
      </p>
    )
  }

  if (prOutcome?.kind === 'stale') {
    return <p className="edit-pr-stale">превью устарело — сгенерируйте правку заново</p>
  }

  if (prOutcome?.kind === 'error') {
    return <p className="edit-error">{prOutcome.message}</p>
  }

  return (
    <button type="button" className="edit-confirm-pr" onClick={onConfirmPr} disabled={prSending}>
      {prSending ? 'открываю PR…' : 'Подтвердить и открыть PR'}
    </button>
  )
}

// Классификация строки unified diff для подсветки: добавленные/удалённые строки и
// метаданные (заголовки хунков/файлов) — прочее рендерим обычным текстом.
function diffLineClass(line: string): string {
  if (line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++')) {
    return 'diff-meta'
  }
  if (line.startsWith('+')) {
    return 'diff-add'
  }
  if (line.startsWith('-')) {
    return 'diff-del'
  }
  return 'diff-ctx'
}

export default EditPanel
