import HealthIndicator from './components/HealthIndicator'

function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>jWorkPlace</h1>
        <p className="subtitle">AI code-assistant поверх произвольного GitHub-репозитория</p>
      </header>
      <main>
        <HealthIndicator />
      </main>
    </div>
  )
}

export default App
