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
  // Прогресс индексации (эмбеддинга): done из total чанков. Осмысленны только при status==='indexing'.
  progress_done?: number
  progress_total?: number
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

// Структура проекта (Задание 1): детерминированный вывод индекса (files + символы из chunks),
// БЕЗ LLM/RAG. Источник — БД, поэтому список консистентен с тем, что «видит» ассистент.
export interface SymbolInfo {
  symbol: string
  kind: string | null
  start_line: number
  end_line: number
}

export interface FileNode {
  path: string
  lang: string | null
  size: number | null
  excluded: boolean
  symbols: SymbolInfo[]
}

export interface ProjectStructure {
  project_id: string
  name: string
  file_count: number
  symbol_count: number
  files: FileNode[]
}

// Ассистент поддержки (Задание 2): ответ по FAQ продукта с опциональным контекстом тикета (MCP).
// escalate=true — в документации ответа нет, обращение уходит человеку. ticket_applied — был ли
// учтён контекст тикета (сам тикет клиенту не возвращаем).
export interface SupportSource {
  file: string
  section: string
  citation: string
  quote: string
}

export interface SupportResponse {
  answer: string
  escalate: boolean
  sources: SupportSource[]
  ticket_applied: boolean
}

// Файловый tool-агент (Задание 3): агент сам комбинирует чтение/поиск/правки под цель.
// needs_pr=false — задача только на чтение (result_text, без PR). needs_pr=true — есть применимый
// diff и run_id: сервер держит diff у себя, подтверждаем по run_id (не шлём diff обратно).
export interface AgentSource {
  file: string
  reason: string
  citation: string
}

export interface AgentRunResponse {
  ok: boolean
  needs_pr: boolean
  run_id?: string
  can_edit?: boolean
  diff?: string
  result_text: string
  sources: AgentSource[]
}

export type AgentPrResult = { status: number } & (
  | { ok: true; pr_url: string }
  | { ok: false; reason: string }
)

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

// База знаний / выжимка о проекте: концепт уже известен пользователю — показываем только имя.
export interface ConceptMention {
  name: string
}

// Новый (ещё не известный) концепт — раскрываем подробно, с обоснованием по коду проекта.
export interface ConceptEvidence {
  citation: string
  quote: string
}

export interface ConceptDetail {
  name: string
  detail: string
  evidence: ConceptEvidence[]
}

// Глобальный каталог «что я уже знаю» (GET /api/knowledge/concepts, опциональная панель).
export interface KnownConcept {
  name: string
  category: string
}

// Дискриминированный union по status: generating — идёт первая генерация (поллинг), error — сбой
// генерации (сообщение уже безопасно для показа), ready — есть выжимка + разбор new/known.
export type ProjectSummary =
  | { status: 'generating' }
  | { status: 'error'; reason: string }
  | {
      status: 'ready'
      overview: string
      tech: string[]
      concepts: {
        new: ConceptDetail[]
        known: ConceptMention[]
      }
    }
