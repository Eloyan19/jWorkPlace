import AgentPanel from './components/AgentPanel'
import ChatPanel from './components/ChatPanel'
import EditPanel from './components/EditPanel'
import HealthIndicator from './components/HealthIndicator'
import ProjectsPanel from './components/ProjectsPanel'
import SearchPanel from './components/SearchPanel'
import StructurePanel from './components/StructurePanel'
import SupportPanel from './components/SupportPanel'

function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>jWorkPlace</h1>
        <p className="subtitle">AI code-assistant поверх произвольного GitHub-репозитория</p>
      </header>
      <main>
        <HealthIndicator />
        <ProjectsPanel />
        <ChatPanel />
        <StructurePanel />
        <EditPanel />
        <AgentPanel />
        <SearchPanel />
        <SupportPanel />
      </main>
    </div>
  )
}

export default App
