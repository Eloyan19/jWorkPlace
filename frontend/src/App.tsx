import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { readActiveTab, writeActiveTab } from './activeTab'
import { readActiveProject, subscribeActiveProject } from './activeProject'
import ActiveProjectIndicator from './components/ActiveProjectIndicator'
import ChatPanel from './components/ChatPanel'
import EditsPanel from './components/EditsPanel'
import EmptyState from './components/EmptyState'
import FaqPanel from './components/FaqPanel'
import HealthIndicator from './components/HealthIndicator'
import ProjectsPanel from './components/ProjectsPanel'
import SearchPanel from './components/SearchPanel'
import StructurePanel from './components/StructurePanel'
import SummaryPanel from './components/SummaryPanel'
import SupportPanel from './components/SupportPanel'

const EMPTY_STATE_MESSAGE = 'Проект не выбран'
const EMPTY_STATE_HINT = 'Выберите проиндексированный проект в панели проектов выше, чтобы начать'

type Tab = 'chat' | 'summary' | 'structure' | 'search' | 'edits' | 'faq' | 'support'

// Проектные вкладки — контекст активного проекта-репо (переключаются вместе с ним).
const PROJECT_TABS: { key: Tab; label: string }[] = [
  { key: 'chat', label: 'Чат' },
  { key: 'summary', label: 'О проекте' },
  { key: 'structure', label: 'Структура' },
  { key: 'search', label: 'Поиск' },
  { key: 'edits', label: 'Правки' },
]

// Сервис-уровневые вкладки — НЕ привязаны к активному проекту («Справка» о самом сервисе,
// «Поддержка сервиса» — про сам jWorkPlace, не про репозиторий). Обе прижаты к правому краю
// таб-бара + разделитель, чтобы не читаться как ещё одна панель по репозиторию.
const SERVICE_TABS: { key: Tab; label: string }[] = [
  { key: 'faq', label: 'Справка' },
  { key: 'support', label: 'Поддержка сервиса' },
]
const SERVICE_TAB_KEYS: readonly Tab[] = SERVICE_TABS.map((t) => t.key)

const TABS = [...PROJECT_TABS, ...SERVICE_TABS]
const TAB_KEYS = TABS.map((t) => t.key)

function isServiceTab(key: Tab): boolean {
  return SERVICE_TAB_KEYS.includes(key)
}

function App() {
  const [tab, setTab] = useState<Tab>(() => readActiveTab(TAB_KEYS, 'chat'))
  // Активный проект — источник правды для гейта проектных вкладок (EmptyState, пока не выбран).
  // Сервисные вкладки (см. SERVICE_TABS) от него не зависят.
  const [activeProjectId, setActiveProjectId] = useState<string | null>(() => readActiveProject())
  // Roving tabindex (WAI-ARIA Tabs): рефы кнопок по ключу вкладки, чтобы после смены стрелкой
  // перенести туда фокус (.focus()) — иначе фокус останется на старой (теперь tabIndex=-1) кнопке.
  const tabRefs = useRef(new Map<Tab, HTMLButtonElement>())

  useEffect(() => {
    return subscribeActiveProject(setActiveProjectId)
  }, [])

  function selectTab(next: Tab) {
    setTab(next)
    writeActiveTab(next)
  }

  // Один обработчик на весь tablist: стрелки/Home/End по общему кольцу TABS (проектные +
  // сервисные), с переносом на краях. Активация — та же selectTab, что и по клику (персист не расходится).
  function handleTabsKeyDown(e: KeyboardEvent<HTMLElement>) {
    const currentIndex = TAB_KEYS.indexOf(tab)
    let nextIndex: number
    switch (e.key) {
      case 'ArrowRight':
        nextIndex = (currentIndex + 1) % TAB_KEYS.length
        break
      case 'ArrowLeft':
        nextIndex = (currentIndex - 1 + TAB_KEYS.length) % TAB_KEYS.length
        break
      case 'Home':
        nextIndex = 0
        break
      case 'End':
        nextIndex = TAB_KEYS.length - 1
        break
      default:
        return
    }
    e.preventDefault()
    const nextKey = TAB_KEYS[nextIndex]
    selectTab(nextKey)
    tabRefs.current.get(nextKey)?.focus()
  }

  function registerTabRef(key: Tab) {
    return (el: HTMLButtonElement | null) => {
      if (el) tabRefs.current.set(key, el)
      else tabRefs.current.delete(key)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>jWorkPlace</h1>
        <p className="subtitle">AI code-assistant поверх произвольного GitHub-репозитория</p>
      </header>
      <main>
        {/* Контекст — всегда на виду: статус сервиса и выбор активного проекта. */}
        <HealthIndicator />
        <ProjectsPanel />

        {/* Один tablist на все вкладки (клавиатура/скринридер видят их как одну группу) —
            отделение сервисных вкладок («Справка», «Поддержка сервиса») чисто визуальное
            (margin-left: auto + разделитель), см. .tab-service в index.css. */}
        <nav className="tabs" role="tablist" aria-label="разделы" onKeyDown={handleTabsKeyDown}>
          {PROJECT_TABS.map((t) => (
            <button
              key={t.key}
              ref={registerTabRef(t.key)}
              role="tab"
              aria-selected={tab === t.key}
              tabIndex={tab === t.key ? 0 : -1}
              className={`tab${tab === t.key ? ' tab-active' : ''}`}
              onClick={() => selectTab(t.key)}
            >
              {t.label}
            </button>
          ))}
          {SERVICE_TABS.map((t) => (
            <button
              key={t.key}
              ref={registerTabRef(t.key)}
              role="tab"
              aria-selected={tab === t.key}
              tabIndex={tab === t.key ? 0 : -1}
              className={`tab tab-service${tab === t.key ? ' tab-active' : ''}`}
              onClick={() => selectTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        {/* Явно, к какому проекту относится содержимое проектных вкладок — не показываем на
            сервисных вкладках («Справка»/«Поддержка сервиса», они не про репозиторий) и не
            монтируем без активного проекта (сам компонент тоже отдаёт null без activeId —
            двойная защита не помешает). */}
        {!isServiceTab(tab) && <ActiveProjectIndicator />}

        {/* Панели остаются смонтированными, неактивные скрыты (hidden) — так не теряется
            состояние (история чата, результаты поиска, превью правки) при переключении вкладок.
            Без активного проекта панели вообще не монтируем — терять там нечего, а показываем
            единый EmptyState вместо N разных собственных заглушек панелей. */}
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'chat'}>
          {activeProjectId ? (
            <ChatPanel />
          ) : (
            <EmptyState message={EMPTY_STATE_MESSAGE} hint={EMPTY_STATE_HINT} />
          )}
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'summary'}>
          {activeProjectId ? (
            <SummaryPanel active={tab === 'summary'} />
          ) : (
            <EmptyState message={EMPTY_STATE_MESSAGE} hint={EMPTY_STATE_HINT} />
          )}
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'structure'}>
          {activeProjectId ? (
            <StructurePanel />
          ) : (
            <EmptyState message={EMPTY_STATE_MESSAGE} hint={EMPTY_STATE_HINT} />
          )}
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'search'}>
          {activeProjectId ? (
            <SearchPanel />
          ) : (
            <EmptyState message={EMPTY_STATE_MESSAGE} hint={EMPTY_STATE_HINT} />
          )}
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'edits'}>
          {activeProjectId ? (
            <EditsPanel />
          ) : (
            <EmptyState message={EMPTY_STATE_MESSAGE} hint={EMPTY_STATE_HINT} />
          )}
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'faq'}>
          <FaqPanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'support'}>
          <SupportPanel />
        </div>
      </main>
    </div>
  )
}

export default App
