import { useEffect, useState } from 'react'
import { getHealth } from '../api'
import type { Health } from '../types'

const POLL_INTERVAL_MS = 10_000

type Status = 'checking' | 'online' | 'offline'

function HealthIndicator() {
  const [status, setStatus] = useState<Status>('checking')
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    let cancelled = false

    async function check() {
      try {
        const result = await getHealth()
        if (cancelled) return
        setHealth(result)
        setStatus('online')
      } catch {
        if (cancelled) return
        setHealth(null)
        setStatus('offline')
      }
    }

    check()
    const timer = setInterval(check, POLL_INTERVAL_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  if (status === 'checking') {
    return (
      <div className="health health-checking" role="status">
        <span className="health-dot" />
        <span>проверка backend…</span>
      </div>
    )
  }

  if (status === 'online') {
    return (
      <div className="health health-online" role="status">
        <span className="health-dot" />
        <span>backend online{health?.version ? ` · v${health.version}` : ''}</span>
      </div>
    )
  }

  return (
    <div className="health health-offline" role="status">
      <span className="health-dot" />
      <span>backend offline</span>
    </div>
  )
}

export default HealthIndicator
