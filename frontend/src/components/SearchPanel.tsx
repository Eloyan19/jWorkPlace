import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { getProject, searchCode } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { Project, SearchHit, SearchResponse } from '../types'

const POLL_INTERVAL_MS = 2_000

function SearchPanel() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [result, setResult] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // Свежий activeId для сверки в асинхронном handleSearch (защита от гонки при переключении).
  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  // Смена активного проекта в ProjectsPanel → сбрасываем прошлый результат поиска.
  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setResult(null)
      setError(null)
    })
  }, [])

  // Детали активного проекта (имя/статус). Пока не ready — поллим, чтобы поймать переход в ready.
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

  async function handleSearch(e: FormEvent) {
    e.preventDefault()
    const trimmed = query.trim()
    if (!trimmed || !activeId) return
    const searchedId = activeId
    setSearching(true)
    setError(null)
    try {
      const res = await searchCode(searchedId, trimmed)
      // Игнорируем ответ, если проект переключили, пока запрос летел (иначе покажем чужие хиты).
      if (mountedRef.current && activeIdRef.current === searchedId) setResult(res)
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === searchedId) {
        setError(err instanceof Error ? err.message : 'не удалось выполнить поиск')
        setResult(null)
      }
    } finally {
      if (mountedRef.current) setSearching(false)
    }
  }

  return (
    <section className="search-panel">
      <h2>Поиск по коду</h2>

      {!activeId ? (
        <p className="search-hint">выберите готовый проект в списке выше</p>
      ) : notReady ? (
        <p className="search-hint">
          проект {project?.name ? `«${project.name}» ` : ''}ещё индексируется — дождитесь статуса «готов»
        </p>
      ) : (
        <>
          <form className="search-form" onSubmit={handleSearch}>
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="что делает функция X, где вызывается Y…"
              aria-label="поисковый запрос по коду"
              disabled={searching}
            />
            <button type="submit" disabled={searching || !query.trim()}>
              {searching ? 'поиск…' : 'Искать'}
            </button>
          </form>

          {error && <p className="search-error">{error}</p>}

          {result && !error && (
            result.abstain ? (
              <p className="search-abstain">
                ничего релевантного не найдено{result.abstain_reason ? ` (${result.abstain_reason})` : ''}.
                Уточните запрос.
              </p>
            ) : (
              <ol className="search-results">
                {result.hits.map((hit, i) => (
                  <ResultCard key={`${hit.citation}-${i}`} hit={hit} />
                ))}
              </ol>
            )
          )}
        </>
      )}
    </section>
  )
}

function fmtScore(v: number | null): string {
  return v === null ? '—' : v.toFixed(3)
}

function ResultCard({ hit }: { hit: SearchHit }) {
  return (
    <li className="result-card">
      <div className="result-header">
        <span className="result-citation">{hit.citation}</span>
        <span className="result-scores" title="dense (cosine) / bm25 / rrf">
          dense {fmtScore(hit.dense_score)} · bm25 {fmtScore(hit.bm25_score)} · rrf {hit.rrf_score.toFixed(4)}
        </span>
      </div>
      <pre className="result-snippet">
        <code>{hit.text}</code>
      </pre>
    </li>
  )
}

export default SearchPanel
