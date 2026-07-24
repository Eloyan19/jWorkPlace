import { useCallback, useEffect, useRef, useState } from 'react'
import { getProject, getSummary, markSummaryRead } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { Project, ProjectSummary } from '../types'

const POLL_INTERVAL_MS = 2_000

// Панель «О проекте»: выжимка репо (что делает, технологии/паттерны) + персонализация обучения —
// НОВЫЕ концепты раскрыты подробно (с обоснованием по коду), уже ЗНАКОМЫЕ — только упомянуты.
// Проп active = открыта ли эта вкладка сейчас: загрузка и поллинг идут только пока панель на виду,
// иначе безусловный auto-load пометил бы концепты «известными» без того, чтобы пользователь их видел.
function SummaryPanel({ active }: { active: boolean }) {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [summary, setSummary] = useState<ProjectSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedNew, setExpandedNew] = useState<Set<string>>(new Set())

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  // project_id, для которого уже вызван markSummaryRead — не звать повторно, пока проект не сменится.
  const markedRef = useRef<string | null>(null)

  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setProject(null)
      setSummary(null)
      setError(null)
      setExpandedNew(new Set())
      markedRef.current = null
    })
  }, [])

  const loadProject = useCallback(async () => {
    if (!activeId) {
      setProject(null)
      return
    }
    try {
      const p = await getProject(activeId)
      if (mountedRef.current && activeIdRef.current === activeId) setProject(p)
    } catch {
      if (mountedRef.current && activeIdRef.current === activeId) setProject(null)
    }
  }, [activeId])

  useEffect(() => {
    if (!active) return
    loadProject()
  }, [active, loadProject])

  const projectReady = project?.status === 'ready'
  const projectFailed = project?.status === 'error'
  const indexing = project !== null && !projectReady && !projectFailed

  // Пока проект ещё индексируется, следим за статусом (только пока вкладка открыта) — как только
  // станет «готов», сработает эффект ниже и загрузит саму выжимку.
  useEffect(() => {
    if (!active || !indexing) return
    const timer = setInterval(loadProject, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [active, indexing, loadProject])

  const loadSummary = useCallback(async () => {
    if (!activeId) return
    const loadedId = activeId
    try {
      const res = await getSummary(loadedId)
      if (!mountedRef.current || activeIdRef.current !== loadedId) return
      setSummary(res)
      setError(null)
      if (res.status === 'ready' && res.concepts.new.length > 0 && markedRef.current !== loadedId) {
        markedRef.current = loadedId
        markSummaryRead(loadedId).catch(() => {
          // сеть подвела — выжимку пользователь уже увидел, при следующем открытии попробуем снова
          if (activeIdRef.current === loadedId) markedRef.current = null
        })
      }
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === loadedId) {
        setError(err instanceof Error ? err.message : 'не удалось загрузить выжимку')
        setSummary(null)
      }
    }
  }, [activeId])

  useEffect(() => {
    if (!active || !projectReady) return
    loadSummary()
  }, [active, projectReady, loadSummary])

  // Генерация выжимки на backend асинхронна (1 вызов LLM) — пока status:"generating", поллим.
  useEffect(() => {
    if (!active || !projectReady || summary?.status !== 'generating') return
    const timer = setInterval(loadSummary, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [active, projectReady, summary?.status, loadSummary])

  const toggleNew = useCallback((name: string) => {
    setExpandedNew((prev) => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  }, [])

  return (
    <section className="summary-panel">
      <h2>О проекте</h2>

      {!activeId ? (
        <p className="summary-hint">выберите готовый проект в списке выше</p>
      ) : project === null ? (
        <p className="summary-hint">проверяем проект…</p>
      ) : indexing ? (
        <p className="summary-hint">
          проект {project?.name ? `«${project.name}» ` : ''}ещё индексируется — дождитесь статуса «готов»
        </p>
      ) : projectFailed ? (
        <p className="summary-hint">проект не проиндексирован (ошибка индексации)</p>
      ) : error ? (
        <p className="summary-error">{error}</p>
      ) : !summary || summary.status === 'generating' ? (
        <p className="summary-hint">формируем выжимку о проекте…</p>
      ) : summary.status === 'error' ? (
        <p className="summary-error">{summary.reason}</p>
      ) : (
        <>
          <p className="summary-overview">{summary.overview}</p>

          {summary.tech.length > 0 && (
            <ul className="summary-tech">
              {summary.tech.map((t) => (
                <li key={t} className="summary-chip">
                  {t}
                </li>
              ))}
            </ul>
          )}

          {summary.concepts.new.length > 0 && (
            <div className="summary-section">
              <h3>Новое для вас</h3>
              <ul className="summary-concept-list">
                {summary.concepts.new.map((c) => {
                  const isOpen = expandedNew.has(c.name)
                  return (
                    <li key={c.name} className="summary-concept">
                      <button
                        type="button"
                        className="summary-concept-toggle"
                        onClick={() => toggleNew(c.name)}
                        aria-expanded={isOpen}
                      >
                        <span className="tree-caret">{isOpen ? '▾' : '▸'}</span>
                        {c.name}
                      </button>
                      {isOpen && (
                        <div className="summary-concept-detail">
                          <p>{c.detail}</p>
                          {c.evidence.length > 0 && (
                            <ul className="summary-evidence">
                              {c.evidence.map((e, i) => (
                                <li key={`${e.citation}-${i}`} className="summary-evidence-item">
                                  <div className="summary-evidence-citation">{e.citation}</div>
                                  <pre className="summary-evidence-quote">
                                    <code>{e.quote}</code>
                                  </pre>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          )}

          {summary.concepts.known.length > 0 && (
            <div className="summary-section">
              <h3>Уже знакомо</h3>
              <ul className="summary-known">
                {summary.concepts.known.map((k) => (
                  <li key={k.name} className="summary-chip summary-chip-known">
                    {k.name}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  )
}

export default SummaryPanel
