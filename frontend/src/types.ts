export interface Health {
  status: string
  version: string
}

export type ProjectStatus = 'cloning' | 'scanning' | 'indexing' | 'ready' | 'error'

export interface Project {
  id: string
  url: string
  name: string
  status: ProjectStatus
  error?: string | null
  indexed_at?: string | null
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
