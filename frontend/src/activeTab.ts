// Персистентность активной вкладки между перезагрузками страницы. Только localStorage — вкладка
// не разделяется между панелями/табами браузера, поэтому CustomEvent (как в activeProject.ts) не нужен.
const KEY = 'jwp_active_tab'

function hasLocalStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

export function readActiveTab<T extends string>(validTabs: readonly T[], fallback: T): T {
  if (!hasLocalStorage()) return fallback
  const stored = window.localStorage.getItem(KEY)
  return stored !== null && (validTabs as readonly string[]).includes(stored) ? (stored as T) : fallback
}

export function writeActiveTab(tab: string): void {
  if (!hasLocalStorage()) return
  window.localStorage.setItem(KEY, tab)
}
