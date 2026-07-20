import { useCallback, useEffect, useRef, useState } from 'react'
import { getProject, getStructure } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'
import type { FileNode, Project, ProjectStructure } from '../types'

const POLL_INTERVAL_MS = 2_000

// Структура проекта (Задание 1): дерево файлов + символы из индекса. Грузим по кнопке (не
// автоматически) — дерево может быть большим, а нужно не всегда. Данные детерминированы (БД),
// LLM/RAG не участвуют.
function StructurePanel() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [project, setProject] = useState<Project | null>(null)
  const [structure, setStructure] = useState<ProjectStructure | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  // Смена активного проекта → сбрасываем прошлую структуру (контексты проектов не смешиваем).
  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setStructure(null)
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

  async function handleLoad() {
    if (!activeId) return
    const loadedId = activeId
    setLoading(true)
    setError(null)
    try {
      const res = await getStructure(loadedId)
      if (mountedRef.current && activeIdRef.current === loadedId) setStructure(res)
    } catch (err) {
      if (mountedRef.current && activeIdRef.current === loadedId) {
        setError(err instanceof Error ? err.message : 'не удалось загрузить структуру')
        setStructure(null)
      }
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }

  return (
    <section className="structure-panel">
      <h2>Структура проекта</h2>

      {!activeId ? (
        <p className="structure-hint">выберите готовый проект в списке выше</p>
      ) : notReady ? (
        <p className="structure-hint">
          проект {project?.name ? `«${project.name}» ` : ''}ещё индексируется — дождитесь статуса «готов»
        </p>
      ) : (
        <>
          <button type="button" onClick={handleLoad} disabled={loading}>
            {loading ? 'загрузка…' : structure ? 'Обновить структуру' : 'Показать структуру'}
          </button>

          {error && <p className="structure-error">{error}</p>}

          {structure && !error && (
            <>
              <p className="structure-summary">
                {structure.file_count} файлов · {structure.symbol_count} символов в индексе
              </p>
              <ul className="structure-files">
                {structure.files.map((f) => (
                  <FileRow key={f.path} node={f} />
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  )
}

function FileRow({ node }: { node: FileNode }) {
  return (
    <li className="structure-file">
      <div className="structure-file-header">
        <span className="structure-file-path">{node.path}</span>
        {node.lang && <span className="structure-file-lang">{node.lang}</span>}
        {node.excluded && (
          <span className="structure-file-excluded" title="файл исключён из индекса (находки скана секретов)">
            исключён
          </span>
        )}
      </div>
      {node.symbols.length > 0 && (
        <ul className="structure-symbols">
          {node.symbols.map((s, i) => (
            <li key={`${s.symbol}-${s.start_line}-${i}`} className="structure-symbol">
              {s.kind && <span className="structure-symbol-kind">{s.kind}</span>}
              <span className="structure-symbol-name">{s.symbol}</span>
              <span className="structure-symbol-lines">
                {s.start_line}–{s.end_line}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

export default StructurePanel
