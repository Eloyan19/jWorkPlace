// Общий «активный проект» для ProjectsPanel и SearchPanel. Живёт в localStorage (переживает
// перезагрузку) + CustomEvent на window, чтобы смена выбора в одной панели сразу дошла до другой
// (события 'storage' в ТОМ ЖЕ табе не срабатывают, поэтому шлём своё).
const KEY = 'jwp_active_project'
const EVENT = 'jwp:active-project'

function hasLocalStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

export function readActiveProject(): string | null {
  return hasLocalStorage() ? window.localStorage.getItem(KEY) : null
}

export function writeActiveProject(id: string): void {
  if (!hasLocalStorage()) return
  window.localStorage.setItem(KEY, id)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: id }))
}

export function clearActiveProject(): void {
  if (!hasLocalStorage()) return
  window.localStorage.removeItem(KEY)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: null }))
}

export function subscribeActiveProject(cb: (id: string | null) => void): () => void {
  if (typeof window === 'undefined') return () => {}
  const handler = (e: Event) => cb((e as CustomEvent<string | null>).detail ?? null)
  window.addEventListener(EVENT, handler)
  return () => window.removeEventListener(EVENT, handler)
}
