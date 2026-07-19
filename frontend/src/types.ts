export interface Health {
  status: string
  version: string
}

export type ProjectStatus = 'cloning' | 'scanning' | 'indexing' | 'ready' | 'error'

// can_edit (Этап 3b) — свойство проекта, не сервиса: true, если проекту привязан валидный
// per-project GitHub PAT (шифруется at rest на backend, сюда токен никогда не приходит).
export interface Project {
  id: string
  url: string
  name: string
  status: ProjectStatus
  error?: string | null
  indexed_at?: string | null
  can_edit: boolean
}

// Один фрагмент кода из hybrid search (Этап 2a). dense_score/bm25_score — сырые скоры каналов
// (null, если чанк пришёл только из другого канала); показываем их для отладки качества поиска.
export interface SearchHit {
  file: string
  symbol: string | null
  symbol_kind: string | null
  lang: string | null
  start_line: number
  end_line: number
  citation: string
  dense_score: number | null
  bm25_score: number | null
  rrf_score: number
  text: string
}

export interface SearchResponse {
  project_id: string
  query: string
  k: number
  abstain: boolean
  abstain_reason: string | null
  hits: SearchHit[]
}

// Grounded-чат по коду (Этап 2b). История держится на фронте one-shot (retrieve по последнему
// вопросу) — backend не хранит диалог между запросами.
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

// Источник ответа: цитата дословно по диапазону строк файла (валидировано backend'ом).
export interface ChatSource {
  id: number
  file: string
  symbol: string | null
  lines: string
  citation: string
  quote: string
}

export interface ChatResponse {
  answer: string
  abstain: boolean
  sources: ChatSource[]
}

// Правка → предпросмотр diff (Этап 3a). Источник тот же формат цитаты, что в чате, но без
// поля id (правка не нумерует источники по ссылкам в тексте ответа).
export interface EditSource {
  file: string
  citation: string
  quote: string
}

export interface EditItem {
  file: string
  reason: string
}

// Union по полю ok: успех несёт diff/summary/edits/sources, отказ — только reason.
export type EditResponse =
  | {
      ok: true
      summary: string
      diff: string
      edits: EditItem[]
      sources: EditSource[]
      dropped: number
    }
  | {
      ok: false
      reason: string
    }

// Реальный PR (Этап 3b). Сервер регенерирует diff и сверяет с показанным пользователю —
// расхождение (проект переиндексирован/файлы изменились между предпросмотром и подтверждением)
// возвращается как ok:false с reason (HTTP 409, см. api.ts::createPr).
export type PrResponse =
  | {
      ok: true
      pr_url: string
    }
  | {
      ok: false
      reason: string
    }
