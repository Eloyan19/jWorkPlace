import type { Health } from './types'

// Инвариант: только относительный путь. В dev его проксирует Vite (vite.config.ts ->
// server.proxy['/api']), в проде — nginx (server_name jwork.jorchik.com, /api/* -> :8200).
// Абсолютный backend-URL здесь не хардкодим — иначе за nginx health будет красным.
export async function getHealth(): Promise<Health> {
  const res = await fetch('/api/health')
  if (!res.ok) {
    throw new Error(`Backend error ${res.status}`)
  }
  return (await res.json()) as Health
}
