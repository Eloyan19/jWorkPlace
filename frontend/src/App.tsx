import { useRef, useState, type KeyboardEvent } from 'react'
import { readActiveTab, writeActiveTab } from './activeTab'
import ChatPanel from './components/ChatPanel'
import EditsPanel from './components/EditsPanel'
import HealthIndicator from './components/HealthIndicator'
import ProjectsPanel from './components/ProjectsPanel'
import SearchPanel from './components/SearchPanel'
import StructurePanel from './components/StructurePanel'
import SummaryPanel from './components/SummaryPanel'
import SupportPanel from './components/SupportPanel'

type Tab = 'chat' | 'summary' | 'structure' | 'search' | 'edits' | 'support'

// Проектные вкладки — контекст активного проекта-репо (переключаются вместе с ним).
const PROJECT_TABS: { key: Tab; label: string }[] = [
  { key: 'chat', label: 'Чат' },
  { key: 'summary', label: 'О проекте' },
  { key: 'structure', label: 'Структура' },
  { key: 'search', label: 'Поиск' },
  { key: 'edits', label: 'Правки' },
]

// «Поддержка сервиса» — единственная НЕ привязанная к активному проекту вкладка (FAQ о самом
// jWorkPlace). Прижата к правому краю таб-бара + разделитель, чтобы не читаться как ещё одна
// панель по репозиторию.
const SUPPORT_TAB: { key: Tab; label: string } = { key: 'support', label: 'Поддержка сервиса' }

const TABS = [...PROJECT_TABS, SUPPORT_TAB]
const TAB_KEYS = TABS.map((t) => t.key)

function App() {
  const [tab, setTab] = useState<Tab>(() => readActiveTab(TAB_KEYS, 'chat'))
  // Roving tabindex (WAI-ARIA Tabs): рефы кнопок по ключу вкладки, чтобы после смены стрелкой
  // перенести туда фокус (.focus()) — иначе фокус останется на старой (теперь tabIndex=-1) кнопке.
  const tabRefs = useRef(new Map<Tab, HTMLButtonElement>())

  function selectTab(next: Tab) {
    setTab(next)
    writeActiveTab(next)
  }

  // Один обработчик на весь tablist: стрелки/Home/End по общему кольцу TABS (project + support),
  // с переносом на краях. Активация — та же selectTab, что и по клику (персист не расходится).
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
            отделение вкладки «Поддержка сервиса» чисто визуальное (margin-left: auto + разделитель). */}
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
          <button
            ref={registerTabRef(SUPPORT_TAB.key)}
            role="tab"
            aria-selected={tab === SUPPORT_TAB.key}
            tabIndex={tab === SUPPORT_TAB.key ? 0 : -1}
            className={`tab tab-support${tab === SUPPORT_TAB.key ? ' tab-active' : ''}`}
            onClick={() => selectTab(SUPPORT_TAB.key)}
          >
            {SUPPORT_TAB.label}
          </button>
        </nav>

        {/* Панели остаются смонтированными, неактивные скрыты (hidden) — так не теряется
            состояние (история чата, результаты поиска, превью правки) при переключении вкладок. */}
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'chat'}>
          <ChatPanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'summary'}>
          <SummaryPanel active={tab === 'summary'} />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'structure'}>
          <StructurePanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'search'}>
          <SearchPanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'edits'}>
          <EditsPanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'support'}>
          <SupportPanel />
        </div>
      </main>
    </div>
  )
}

export default App
