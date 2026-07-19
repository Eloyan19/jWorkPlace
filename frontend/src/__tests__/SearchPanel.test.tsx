import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SearchPanel from '../components/SearchPanel'
import * as api from '../api'
import type { Project, SearchResponse } from '../types'

vi.mock('../api')

const mockedApi = vi.mocked(api)

function readyProject(): Project {
  return {
    id: 'abc123',
    url: 'u',
    name: 'repo',
    status: 'ready',
    error: null,
    indexed_at: null,
    can_edit: false,
  }
}

function searchResponse(over: Partial<SearchResponse> = {}): SearchResponse {
  return {
    project_id: 'abc123', query: 'striptags', k: 8,
    abstain: false, abstain_reason: null,
    hits: [{
      file: 'src/util.py', symbol: 'striptags', symbol_kind: 'function_definition', lang: 'python',
      start_line: 10, end_line: 20, citation: 'src/util.py::striptags::L10-20',
      dense_score: 0.7, bm25_score: -8, rrf_score: 0.032, text: 'def striptags(s): ...',
    }],
    ...over,
  }
}

describe('SearchPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('без активного проекта показывает подсказку', () => {
    render(<SearchPanel />)
    expect(screen.getByText(/выберите готовый проект/i)).toBeInTheDocument()
  })

  it('для не-готового проекта показывает, что он индексируется', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue({ ...readyProject(), status: 'indexing' })
    render(<SearchPanel />)
    await waitFor(() => expect(screen.getByText(/ещё индексируется/i)).toBeInTheDocument())
  })

  it('рендерит результаты с источником и фрагментом', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.searchCode.mockResolvedValue(searchResponse())

    render(<SearchPanel />)
    await waitFor(() => expect(screen.getByLabelText(/поисковый запрос/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/поисковый запрос/i), { target: { value: 'striptags' } })
    fireEvent.click(screen.getByRole('button', { name: /Искать/i }))

    await waitFor(() => {
      expect(screen.getByText('src/util.py::striptags::L10-20')).toBeInTheDocument()
    })
    expect(screen.getByText(/def striptags/)).toBeInTheDocument()
    expect(mockedApi.searchCode).toHaveBeenCalledWith('abc123', 'striptags')
  })

  it('показывает «не знаю» при abstain', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.searchCode.mockResolvedValue(
      searchResponse({ abstain: true, abstain_reason: 'ничего релевантного не найдено', hits: [] }),
    )

    render(<SearchPanel />)
    await waitFor(() => expect(screen.getByLabelText(/поисковый запрос/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/поисковый запрос/i), { target: { value: 'xyzzy' } })
    fireEvent.click(screen.getByRole('button', { name: /Искать/i }))

    await waitFor(() => {
      expect(screen.getByText(/ничего релевантного не найдено/i)).toBeInTheDocument()
    })
  })

  it('показывает ошибку при сбое поиска', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.searchCode.mockRejectedValue(new Error('backend упал'))

    render(<SearchPanel />)
    await waitFor(() => expect(screen.getByLabelText(/поисковый запрос/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/поисковый запрос/i), { target: { value: 'striptags' } })
    fireEvent.click(screen.getByRole('button', { name: /Искать/i }))

    await waitFor(() => expect(screen.getByText(/backend упал/i)).toBeInTheDocument())
  })
})
