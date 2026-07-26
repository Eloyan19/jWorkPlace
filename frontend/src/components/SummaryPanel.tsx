import { useCallback, useEffect, useRef, useState } from 'react'
import { getProject, getSummary, markConceptKnown, resetKnownConcepts } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { ConceptDetail, Project, ProjectSummary } from '../types'

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

  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setProject(null)
      setSummary(null)
      setError(null)
      setExpandedNew(new Set())
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

  const toggleNew = useCallback((slug: string) => {
    setExpandedNew((prev) => {
      const next = new Set(prev)
      next.has(slug) ? next.delete(slug) : next.add(slug)
      return next
    })
  }, [])

  // Ручная пометка «изучено» по клику — переносим концепт из «нового» в «знакомо» оптимистично,
  // без перезагрузки summary (не теряем раскрытые/прокрутку остальных концептов).
  const handleMarkKnown = useCallback(
    async (concept: ConceptDetail) => {
      const targetId = activeId
      try {
        await markConceptKnown(concept.slug)
        if (!mountedRef.current || activeIdRef.current !== targetId) return
        setSummary((prev) => {
          if (!prev || prev.status !== 'ready') return prev
          return {
            ...prev,
            concepts: {
              new: prev.concepts.new.filter((c) => c.slug !== concept.slug),
              known: [...prev.concepts.known, { name: concept.name }],
            },
          }
        })
        setExpandedNew((prev) => {
          if (!prev.has(concept.slug)) return prev
          const next = new Set(prev)
          next.delete(concept.slug)
          return next
        })
      } catch (err) {
        if (mountedRef.current && activeIdRef.current === targetId) {
          setError(err instanceof Error ? err.message : 'не удалось отметить концепт изученным')
        }
      }
    },
    [activeId],
  )

  // Мягкий сброс базы знаний целиком — после подтверждения перезагружаем summary с backend.
  const handleReset = useCallback(async () => {
    if (!window.confirm('Сбросить базу знаний? Все концепты снова станут «новыми».')) return
    const targetId = activeId
    try {
      await resetKnownConcepts()
      if (mountedRef.current && activeIdRef.current === targetId) loadSummary()
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === targetId) {
        setError(err instanceof Error ? err.message : 'не удалось сбросить базу знаний')
      }
    }
  }, [activeId, loadSummary])

  return (
    <section className="summary-panel">
      <div className="summary-header">
        <h2>О проекте</h2>
        {summary?.status === 'ready' && (
          <button type="button" className="summary-reset-button" onClick={handleReset}>
            Очистить базу знаний
          </button>
        )}
      </div>

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
                  const isOpen = expandedNew.has(c.slug)
                  return (
                    <li key={c.slug} className="summary-concept">
                      <div className="summary-concept-header">
                        <button
                          type="button"
                          className="summary-concept-toggle"
                          onClick={() => toggleNew(c.slug)}
                          aria-expanded={isOpen}
                        >
                          <span className="tree-caret">{isOpen ? '▾' : '▸'}</span>
                          {c.name}
                        </button>
                        <button
                          type="button"
                          className="summary-concept-mark-known"
                          onClick={() => handleMarkKnown(c)}
                        >
                          Изучено
                        </button>
                      </div>
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
