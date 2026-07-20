import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { confirmAgentPr, getProject, runAgent } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { AgentRunResponse, Project } from '../types'

const POLL_INTERVAL_MS = 2_000

// Файловый tool-агент (Задание 3): агент сам изучает проект под цель и, если нужно, готовит правки.
// Задача-чтение → текстовый итог. Задача-изменение → превью diff + подтверждение PR (по run_id).
type PrOutcome =
  | { kind: 'success'; url: string }
  | { kind: 'stale' }
  | { kind: 'error'; message: string }

function AgentPanel() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [goal, setGoal] = useState('')
  const [result, setResult] = useState<AgentRunResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [prSending, setPrSending] = useState(false)
  const [prOutcome, setPrOutcome] = useState<PrOutcome | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

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

  async function handleRun(e: FormEvent) {
    e.preventDefault()
    const trimmed = goal.trim()
    if (!trimmed || !activeId) return
    const sentId = activeId
    setRunning(true)
    setError(null)
    setPrOutcome(null)
    try {
      const res = await runAgent(sentId, trimmed)
      if (mountedRef.current && activeIdRef.current === sentId) setResult(res)
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === sentId) {
        setError(err instanceof Error ? err.message : 'агент не смог выполнить задачу')
        setResult(null)
      }
    } finally {
      if (mountedRef.current) setRunning(false)
    }
  }

  async function handleConfirmPr() {
    if (!activeId || !result?.run_id || prSending) return
    const sentId = activeId
    const runId = result.run_id
    setPrSending(true)
    try {
      const res = await confirmAgentPr(sentId, runId)
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
        setPrOutcome({ kind: 'error', message: err instanceof Error ? err.message : 'не удалось открыть PR' })
      }
    } finally {
      if (mountedRef.current) setPrSending(false)
    }
  }

  return (
    <section className="agent-panel">
      <h2>Агент по файлам</h2>
      <p className="agent-hint-text">
        Задайте цель — агент сам изучит проект и, если нужно, подготовит правки и Pull Request.
        Примеры: «найди все использования X», «сгенерируй CHANGELOG», «проверь инварианты».
      </p>

      {!activeId ? (
        <p className="agent-hint">выберите готовый проект в списке выше</p>
      ) : notReady ? (
        <p className="agent-hint">
          проект {project?.name ? `«${project.name}» ` : ''}ещё индексируется — дождитесь статуса «готов»
        </p>
      ) : (
        <>
          {running && <p className="agent-typing">агент работает…</p>}
          {error && <p className="agent-error">{error}</p>}
          {result && (
            <AgentResult
              result={result}
              prSending={prSending}
              prOutcome={prOutcome}
              onConfirmPr={handleConfirmPr}
            />
          )}

          <form className="agent-form" onSubmit={handleRun}>
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="цель для агента…"
              aria-label="цель для агента"
              disabled={running || prSending}
              rows={2}
              maxLength={2000}
            />
            <button type="submit" disabled={running || prSending || !goal.trim()}>
              {running ? 'работает…' : 'Запустить агента'}
            </button>
          </form>
        </>
      )}
    </section>
  )
}

function AgentResult({
  result,
  prSending,
  prOutcome,
  onConfirmPr,
}: {
  result: AgentRunResponse
  prSending: boolean
  prOutcome: PrOutcome | null
  onConfirmPr: () => void
}) {
  return (
    <div className="agent-result">
      <p className="agent-answer">{result.result_text}</p>

      {result.sources.length > 0 && (
        <ul className="agent-files">
          {result.sources.map((s, i) => (
            <li key={`${s.file}-${i}`}>
              <span className="agent-file-path">{s.file}</span> — {s.reason}
            </li>
          ))}
        </ul>
      )}

      {result.needs_pr && result.diff && (
        <>
          <pre className="edit-diff">
            <code>
              {result.diff.split('\n').map((line, i) => (
                <div key={i} className={diffLineClass(line)}>
                  {line}
                </div>
              ))}
            </code>
          </pre>
          <AgentPrControl
            canEdit={result.can_edit ?? false}
            prSending={prSending}
            prOutcome={prOutcome}
            onConfirmPr={onConfirmPr}
          />
        </>
      )}
    </div>
  )
}

function AgentPrControl({
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
    return <p className="agent-hint agent-pr-hint">включите правки токеном проекта, чтобы открыть PR</p>
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
    return <p className="edit-pr-stale">превью устарело — запустите агента заново</p>
  }
  if (prOutcome?.kind === 'error') {
    return <p className="agent-error">{prOutcome.message}</p>
  }
  return (
    <button type="button" className="edit-confirm-pr" onClick={onConfirmPr} disabled={prSending}>
      {prSending ? 'открываю PR…' : 'Подтвердить и открыть PR'}
    </button>
  )
}

// Классификация строки unified diff для подсветки (тот же вид, что в EditPanel).
function diffLineClass(line: string): string {
  if (line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++') || line.startsWith('diff --git')) {
    return 'diff-meta'
  }
  if (line.startsWith('+')) return 'diff-add'
  if (line.startsWith('-')) return 'diff-del'
  return 'diff-ctx'
}

export default AgentPanel
