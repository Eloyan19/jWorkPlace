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
