import type {
  ChatMessage,
  ChatResponse,
  EditResponse,
  Health,
  PrResponse,
  Project,
  SearchResponse,
} from './types'

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

const TOKEN_STORAGE_KEY = 'jwp_token'

// Токен-барьер (не секрет — реальные секреты живут только на backend): по умолчанию
// из VITE_API_TOKEN (сборочное значение), но ссылкой вида ?token=... можно передать
// свой и он осядет в localStorage, чтобы пережить перезагрузку страницы.
function resolveToken(): string | undefined {
  if (typeof window !== 'undefined' && typeof window.localStorage !== 'undefined') {
    const fromUrl = new URLSearchParams(window.location.search).get('token')
    if (fromUrl) {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, fromUrl)
    }
    const stored = window.localStorage.getItem(TOKEN_STORAGE_KEY)
    if (stored) return stored
  }
  return import.meta.env.VITE_API_TOKEN || undefined
}

export function authHeaders(): Record<string, string> {
  const token = resolveToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function readErrorMessage(res: Response): Promise<string> {
  if (res.status === 401) {
    return 'нет доступа: укажите ?token=<токен> в ссылке'
  }
  try {
    const data = await res.json()
    if (data && typeof data.detail === 'string') {
      return data.detail
    }
  } catch {
    // тело не JSON — падаем на общий текст ниже
  }
  return `Backend error ${res.status}`
}

export async function createProject(url: string): Promise<{ project_id: string; status: string }> {
  const res = await fetch('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ url }),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as { project_id: string; status: string }
}

export async function listProjects(): Promise<Project[]> {
  const res = await fetch('/api/projects', { headers: authHeaders() })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as Project[]
}

export async function getProject(id: string): Promise<Project> {
  const res = await fetch(`/api/projects/${id}`, { headers: authHeaders() })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as Project
}

export async function reindexProject(id: string): Promise<{ status: string }> {
  const res = await fetch(`/api/projects/${id}/reindex`, {
    method: 'POST',
    headers: authHeaders(),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as { status: string }
}

// Включить правки (Этап 3b): backend валидирует токен против самого репо (push-право +
// совпадение full_name) прежде чем сохранить — здесь просто отправляем и разбираем ответ.
// Токен нигде на фронте не сохраняем и не логируем; поле очищаем сразу после вызова (компонент).
export async function putProjectToken(id: string, token: string): Promise<{ can_edit: boolean }> {
  const res = await fetch(`/api/projects/${id}/token`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ token }),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as { can_edit: boolean }
}

export async function deleteProjectToken(id: string): Promise<{ can_edit: boolean }> {
  const res = await fetch(`/api/projects/${id}/token`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as { can_edit: boolean }
}

export async function searchCode(
  projectId: string,
  query: string,
  k = 8,
): Promise<SearchResponse> {
  const res = await fetch('/api/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ project_id: projectId, query, k }),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as SearchResponse
}

export async function sendChat(
  projectId: string,
  messages: ChatMessage[],
): Promise<ChatResponse> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ project_id: projectId, messages }),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as ChatResponse
}

// Правка → предпросмотр diff (Этап 3a). ok:false в теле — не HTTP-ошибка (сервер отказался
// собрать правку по гейту grounding), поэтому проверяем только res.ok (транспорт).
export async function proposeEdit(projectId: string, instruction: string): Promise<EditResponse> {
  const res = await fetch(`/api/projects/${projectId}/edit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ instruction }),
  })
  if (!res.ok) {
    throw new Error(await readErrorMessage(res))
  }
  return (await res.json()) as EditResponse
}

// Реальный PR (Этап 3b). Сервер регенерирует diff по instruction и сверяет с expected_diff
// (тем, что пользователь видел в EditPanel) — расхождение возвращается 409 с тем же телом
// {ok:false, reason}, поэтому это не транспортная ошибка: разбираем наравне с 200.
export type PrResult = { status: number } & PrResponse

export async function createPr(
  projectId: string,
  body: { instruction: string; expected_diff: string },
): Promise<PrResult> {
  const res = await fetch(`/api/projects/${projectId}/pr`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ confirm: true, ...body }),
  })
  if (!res.ok && res.status !== 409) {
    throw new Error(await readErrorMessage(res))
  }
  const data = (await res.json()) as PrResponse
  return { status: res.status, ...data }
}
