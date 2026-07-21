import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  createProject,
  deleteProject,
  deleteProjectToken,
  listProjects,
  putProjectToken,
  rebuildProject,
  reindexProject,
} from '../api'
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

// Метка статуса: при индексации, если известен total, показываем прогресс «индексация N/M».
function statusLabel(project: Project): string {
  if (project.status === 'indexing' && project.progress_total && project.progress_total > 0) {
    return `индексация ${project.progress_done ?? 0}/${project.progress_total}`
  }
  return STATUS_LABELS[project.status]
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
  // Одна операция над строкой проекта за раз (обновить/переиндексировать заново/удалить/reindex у error).
  const [busyId, setBusyId] = useState<string | null>(null)

  // Правки (Этап 3b): токен вводится и отправляется — не храним и не показываем обратно,
  // поле очищается сразу после запроса (успех или провал).
  const [tokenInputs, setTokenInputs] = useState<Record<string, string>>({})
  const [tokenBusy, setTokenBusy] = useState<Record<string, boolean>>({})
  const [tokenError, setTokenError] = useState<Record<string, string | null>>({})

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
    setBusyId(id)
    try {
      await reindexProject(id)
      if (!mountedRef.current) return
      await refresh()
    } catch (err) {
      if (!mountedRef.current) return
      setListError(err instanceof Error ? err.message : 'не удалось запустить переиндексацию')
    } finally {
      if (mountedRef.current) setBusyId(null)
    }
  }

  async function handleRebuild(id: string) {
    if (!window.confirm(
      'Переиндексировать заново? Репозиторий будет склонирован с нуля и полностью переиндексирован.',
    )) return
    setBusyId(id)
    try {
      await rebuildProject(id)
      if (!mountedRef.current) return
      await refresh()
    } catch (err) {
      if (!mountedRef.current) return
      setListError(err instanceof Error ? err.message : 'не удалось переиндексировать заново')
    } finally {
      if (mountedRef.current) setBusyId(null)
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm(
      'Удалить проект? Клон, индекс и все метаданные будут удалены безвозвратно.',
    )) return
    setBusyId(id)
    try {
      await deleteProject(id)
      if (!mountedRef.current) return
      // Удалили активный проект → снимаем выбор (контексты не должны ссылаться на исчезнувший id).
      if (activeIdRef.current === id) {
        clearActiveProject()
        setActiveId(null)
      }
      await refresh()
    } catch (err) {
      if (!mountedRef.current) return
      setListError(err instanceof Error ? err.message : 'не удалось удалить проект')
    } finally {
      if (mountedRef.current) setBusyId(null)
    }
  }

  async function handleEnableEdit(id: string) {
    const token = (tokenInputs[id] ?? '').trim()
    if (!token) return
    setTokenBusy((prev) => ({ ...prev, [id]: true }))
    setTokenError((prev) => ({ ...prev, [id]: null }))
    try {
      await putProjectToken(id, token)
      if (!mountedRef.current) return
      await refresh()
    } catch (err) {
      if (!mountedRef.current) return
      setTokenError((prev) => ({
        ...prev,
        [id]: err instanceof Error ? err.message : 'не удалось включить правки',
      }))
    } finally {
      // Токен не оставляем в поле независимо от исхода (успех/провал) — не пересвечиваем
      // введённое значение. Гвард mountedRef — тот же паттерн, что и в остальных finally
      // этого файла (handleReindex, handleDisableEdit).
      if (mountedRef.current) {
        setTokenInputs((prev) => ({ ...prev, [id]: '' }))
        setTokenBusy((prev) => ({ ...prev, [id]: false }))
      }
    }
  }

  async function handleDisableEdit(id: string) {
    setTokenBusy((prev) => ({ ...prev, [id]: true }))
    setTokenError((prev) => ({ ...prev, [id]: null }))
    try {
      await deleteProjectToken(id)
      if (!mountedRef.current) return
      await refresh()
    } catch (err) {
      if (!mountedRef.current) return
      setTokenError((prev) => ({
        ...prev,
        [id]: err instanceof Error ? err.message : 'не удалось отключить правки',
      }))
    } finally {
      if (mountedRef.current) setTokenBusy((prev) => ({ ...prev, [id]: false }))
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
              <span className={`badge badge-${project.status}`}>{statusLabel(project)}</span>
              {project.status === 'error' && (
                <>
                  {project.error && <span className="project-error-text">{project.error}</span>}
                  <button
                    type="button"
                    className="project-reindex"
                    onClick={() => handleReindex(project.id)}
                    disabled={busyId === project.id}
                  >
                    {busyId === project.id ? 'переиндексация…' : 'Переиндексировать'}
                  </button>
                </>
              )}
              {project.status === 'ready' && (
                <>
                <div className="project-actions">
                  <button
                    type="button"
                    className="project-action"
                    onClick={() => handleReindex(project.id)}
                    disabled={busyId === project.id}
                    title="Быстрое инкрементальное обновление (fetch + переиндексация изменённого)"
                  >
                    {busyId === project.id ? '…' : 'Обновить'}
                  </button>
                  <button
                    type="button"
                    className="project-action"
                    onClick={() => handleRebuild(project.id)}
                    disabled={busyId === project.id}
                    title="Полный re-clone с нуля и переиндексация (для сильно изменившегося репо)"
                  >
                    Переиндексировать заново
                  </button>
                  <button
                    type="button"
                    className="project-action project-action-danger"
                    onClick={() => handleDelete(project.id)}
                    disabled={busyId === project.id}
                    title="Удалить проект: клон, индекс и метаданные"
                  >
                    Удалить
                  </button>
                </div>
                <div className="project-token">
                  <span className={`badge ${project.can_edit ? 'badge-editable' : 'badge-readonly'}`}>
                    {project.can_edit ? '✅ правки включены' : '🔒 read-only'}
                  </span>
                  {project.can_edit ? (
                    <button
                      type="button"
                      className="project-token-disable"
                      onClick={() => handleDisableEdit(project.id)}
                      disabled={tokenBusy[project.id]}
                    >
                      {tokenBusy[project.id] ? 'отключение…' : 'Отключить'}
                    </button>
                  ) : (
                    <form
                      className="project-token-form"
                      onSubmit={(e) => {
                        e.preventDefault()
                        handleEnableEdit(project.id)
                      }}
                    >
                      <input
                        type="password"
                        value={tokenInputs[project.id] ?? ''}
                        onChange={(e) =>
                          setTokenInputs((prev) => ({ ...prev, [project.id]: e.target.value }))
                        }
                        placeholder="fine-grained GitHub PAT"
                        aria-label={`GitHub-токен для ${project.name}`}
                        disabled={tokenBusy[project.id]}
                        autoComplete="off"
                      />
                      <button
                        type="submit"
                        disabled={tokenBusy[project.id] || !(tokenInputs[project.id] ?? '').trim()}
                      >
                        {tokenBusy[project.id] ? 'проверка…' : 'Включить правки'}
                      </button>
                    </form>
                  )}
                  {tokenError[project.id] && (
                    <span className="project-error-text">{tokenError[project.id]}</span>
                  )}
                </div>
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
