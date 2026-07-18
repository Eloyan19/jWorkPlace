import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { createProject, listProjects, reindexProject } from '../api'
import { clearActiveProject, readActiveProject, writeActiveProject } from '../activeProject'
import type { Project, ProjectStatus } from '../types'

const POLL_INTERVAL_MS = 2_000

const IN_PROGRESS_STATUSES: ProjectStatus[] = ['cloning', 'scanning', 'indexing']

const STATUS_LABELS: Record<ProjectStatus, string> = {
  cloning: 'клонирование…',
  scanning: 'сканирование…',
  indexing: 'индексация…',
  ready: 'готов',
  error: 'ошибка',
}

function isInProgress(status: ProjectStatus): boolean {
  return IN_PROGRESS_STATUSES.includes(status)
}

function isValidRepoUrl(url: string): boolean {
  return url.trim().startsWith('https://github.com/')
}

function ProjectsPanel() {
  const [projects, setProjects] = useState<Project[]>([])
  const [url, setUrl] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [reindexingId, setReindexingId] = useState<string | null>(null)

  // Гвард от setState после размонтирования (StrictMode делает mount→unmount→mount) —
  // тот же паттерн, что и cancelled-флаг в HealthIndicator.
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // Не даём тикам поллинга накладываться друг на друга, если backend отвечает
  // дольше POLL_INTERVAL_MS — иначе более старый ответ может перезаписать свежий стейт.
  const inFlightRef = useRef(false)

  const refresh = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    try {
      const result = await listProjects()
      if (!mountedRef.current) return
      setProjects(result)
      setListError(null)
      // Активный проект исчез из списка → снимаем выбор. Побочный эффект (clearActiveProject
      // шлёт событие → setState в SearchPanel) держим ВНЕ updater'а setState: updater обязан быть
      // чистым (в StrictMode зовётся дважды), а обновлять другой компонент из него — анти-паттерн.
      const current = activeIdRef.current
      if (current && !result.some((p) => p.id === current)) {
        clearActiveProject()
        setActiveId(null)
      }
    } catch (err) {
      if (!mountedRef.current) return
      setListError(err instanceof Error ? err.message : 'не удалось получить список проектов')
    } finally {
      inFlightRef.current = false
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // Поллинг статуса, пока хотя бы один проект в процессе — иначе не дёргаем backend впустую.
  const hasInProgress = projects.some((p) => isInProgress(p.status))
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh
  // Свежий activeId для refresh (useCallback с [] иначе замкнул бы устаревшее значение).
  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  useEffect(() => {
    if (!hasInProgress) return
    const timer = setInterval(() => {
      refreshRef.current()
    }, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [hasInProgress])

  function selectProject(id: string) {
    setActiveId(id)
    writeActiveProject(id)
  }

  async function handleConnect(e: FormEvent) {
    e.preventDefault()
    const trimmed = url.trim()
    if (!isValidRepoUrl(trimmed)) {
      setFormError('укажите ссылку вида https://github.com/owner/repo')
      return
    }
    setFormError(null)
    setConnecting(true)
    try {
      await createProject(trimmed)
      if (!mountedRef.current) return
      setUrl('')
      await refresh()
    } catch (err) {
      if (!mountedRef.current) return
      setFormError(err instanceof Error ? err.message : 'не удалось подключить проект')
    } finally {
      if (mountedRef.current) setConnecting(false)
    }
  }

  async function handleReindex(id: string) {
    setReindexingId(id)
    try {
      await reindexProject(id)
      if (!mountedRef.current) return
      await refresh()
    } catch (err) {
      if (!mountedRef.current) return
      setListError(err instanceof Error ? err.message : 'не удалось запустить переиндексацию')
    } finally {
      if (mountedRef.current) setReindexingId(null)
    }
  }

  return (
    <section className="projects-panel">
      <h2>Проекты</h2>
      <form className="projects-form" onSubmit={handleConnect}>
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://github.com/owner/repo"
          aria-label="ссылка на GitHub-репозиторий"
          disabled={connecting}
        />
        <button type="submit" disabled={connecting}>
          {connecting ? 'подключение…' : 'Подключить'}
        </button>
      </form>
      {formError && <p className="projects-error">{formError}</p>}
      {listError && <p className="projects-error">{listError}</p>}

      {projects.length === 0 ? (
        <p className="projects-empty">пока нет проиндексированных проектов</p>
      ) : (
        <ul className="projects-list">
          {projects.map((project) => (
            <li
              key={project.id}
              className={`project-item${project.id === activeId ? ' project-item-active' : ''}`}
            >
              <button
                type="button"
                className="project-select"
                onClick={() => selectProject(project.id)}
                aria-pressed={project.id === activeId}
              >
                <span className="project-name">{project.name}</span>
                <span className="project-url">{project.url}</span>
              </button>
              <span className={`badge badge-${project.status}`}>{STATUS_LABELS[project.status]}</span>
              {project.status === 'error' && (
                <>
                  {project.error && <span className="project-error-text">{project.error}</span>}
                  <button
                    type="button"
                    className="project-reindex"
                    onClick={() => handleReindex(project.id)}
                    disabled={reindexingId === project.id}
                  >
                    {reindexingId === project.id ? 'переиндексация…' : 'Переиндексировать'}
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default ProjectsPanel
