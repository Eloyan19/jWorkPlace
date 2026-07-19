import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { getProject, proposeEdit } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { EditResponse, Project } from '../types'

const POLL_INTERVAL_MS = 2_000

function EditPanel() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [instruction, setInstruction] = useState('')
  const [result, setResult] = useState<EditResponse | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
      }
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === sentId) {
        setError(err instanceof Error ? err.message : 'не удалось предложить правку')
      }
    } finally {
      if (mountedRef.current) setSending(false)
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
          {result && <EditResult result={result} />}

          <form className="edit-form" onSubmit={handleSubmit}>
            <textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="опишите правку: что и где изменить"
              aria-label="описание правки"
              disabled={sending}
              rows={3}
              maxLength={2000}
            />
            <button type="submit" disabled={sending || !instruction.trim()}>
              {sending ? 'отправка…' : 'Предложить правку'}
            </button>
          </form>
        </>
      )}
    </section>
  )
}

function EditResult({ result }: { result: EditResponse }) {
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

      <button
        type="button"
        className="edit-confirm-pr"
        disabled
        title="появится на Этапе 3b"
      >
        Подтвердить и открыть PR
      </button>
    </div>
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
