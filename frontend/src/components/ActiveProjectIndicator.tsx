import { useEffect, useRef, useState } from 'react'
import { getProject } from '../api'
import { readActiveProject, subscribeActiveProject } from '../activeProject'

// Компактный индикатор «к какому проекту относится вкладка» — рендерится над tab-pane только на
// проектных вкладках (App.tsx решает когда монтировать; на «Поддержке сервиса» — никогда).
// activeProject.ts хранит id, не читаемое имя — имя подтягиваем через getProject.
function ActiveProjectIndicator() {
  const [activeId, setActiveId] = useState<string | null>(() => readActiveProject())
  const [name, setName] = useState<string | null>(null)

  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  // Свежий activeId внутри async-колбэка getProject — без него ответ на запрос по СТАРОМУ
  // проекту мог бы затереть имя уже переключённого нового (гонка при быстрой смене).
  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId

  useEffect(() => {
    return subscribeActiveProject((id) => {
      setActiveId(id)
      setName(null)
    })
  }, [])

  useEffect(() => {
    if (!activeId) return
    const requestedId = activeId
    getProject(requestedId)
      .then((project) => {
        if (!mountedRef.current || activeIdRef.current !== requestedId) return
        setName(project.name)
      })
      .catch(() => {
        // Не роняем индикатор из-за сетевой ошибки — просто останется id (см. рендер ниже).
      })
  }, [activeId])

  if (!activeId) return null

  return (
    <div className="active-project-bar" role="status">
      <span className="active-project-icon" aria-hidden="true">
        📁
      </span>
      <span className="active-project-name">{name ?? activeId}</span>
    </div>
  )
}

export default ActiveProjectIndicator
