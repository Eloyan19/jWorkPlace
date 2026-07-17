import HealthIndicator from './components/HealthIndicator'
import ProjectsPanel from './components/ProjectsPanel'

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
      </main>
    </div>
  )
}

export default App
