import { useState } from 'react'
import AgentPanel from './components/AgentPanel'
import ChatPanel from './components/ChatPanel'
import EditPanel from './components/EditPanel'
import HealthIndicator from './components/HealthIndicator'
import ProjectsPanel from './components/ProjectsPanel'
import SearchPanel from './components/SearchPanel'
import StructurePanel from './components/StructurePanel'
import SupportPanel from './components/SupportPanel'

type Tab = 'chat' | 'structure' | 'search' | 'edits' | 'support'

const TABS: { key: Tab; label: string }[] = [
  { key: 'chat', label: 'Чат' },
  { key: 'structure', label: 'Структура' },
  { key: 'search', label: 'Поиск' },
  { key: 'edits', label: 'Правки' },
  { key: 'support', label: 'Поддержка' },
]

function App() {
  const [tab, setTab] = useState<Tab>('chat')

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

        <nav className="tabs" role="tablist" aria-label="разделы">
          {TABS.map((t) => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              className={`tab${tab === t.key ? ' tab-active' : ''}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        {/* Панели остаются смонтированными, неактивные скрыты (hidden) — так не теряется
            состояние (история чата, результаты поиска, превью правки) при переключении вкладок. */}
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'chat'}>
          <ChatPanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'structure'}>
          <StructurePanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'search'}>
          <SearchPanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'edits'}>
          {/* «Правка кода» — быстрый одношаговый патч по известному месту;
              «Агент по файлам» — автономный многошаговый (исследует, создаёт файлы). */}
          <EditPanel />
          <AgentPanel />
        </div>
        <div className="tab-pane" role="tabpanel" hidden={tab !== 'support'}>
          <SupportPanel />
        </div>
      </main>
    </div>
  )
}

export default App
