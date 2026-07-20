import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StructurePanel from '../components/StructurePanel'
import * as api from '../api'
import type { Project, ProjectStructure } from '../types'

vi.mock('../api')

const mockedApi = vi.mocked(api)

function readyProject(): Project {
  return {
    id: 'abc123', url: 'u', name: 'repo', status: 'ready',
    error: null, indexed_at: null, can_edit: false,
  }
}

function structure(): ProjectStructure {
  return {
    project_id: 'abc123', name: 'repo', file_count: 1, symbol_count: 2,
    files: [{
      path: 'src/util.py', lang: 'python', size: 40, excluded: false,
      symbols: [
        { symbol: 'foo', kind: 'function_definition', start_line: 1, end_line: 3 },
        { symbol: 'Bar', kind: 'class_definition', start_line: 5, end_line: 9 },
      ],
    }],
  }
}

describe('StructurePanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('без активного проекта показывает подсказку', () => {
    render(<StructurePanel />)
    expect(screen.getByText(/выберите готовый проект/i)).toBeInTheDocument()
  })

  it('по кнопке грузит и рендерит дерево файлов и символов', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getStructure.mockResolvedValue(structure())

    render(<StructurePanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Показать структуру/i })).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /Показать структуру/i }))

    await waitFor(() => expect(screen.getByText('src/util.py')).toBeInTheDocument())
    expect(screen.getByText('foo')).toBeInTheDocument()
    expect(screen.getByText('Bar')).toBeInTheDocument()
    expect(screen.getByText(/1 файлов · 2 символов/i)).toBeInTheDocument()
    expect(mockedApi.getStructure).toHaveBeenCalledWith('abc123')
  })

  it('показывает ошибку при сбое загрузки', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getStructure.mockRejectedValue(new Error('backend упал'))

    render(<StructurePanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Показать структуру/i })).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /Показать структуру/i }))
    await waitFor(() => expect(screen.getByText(/backend упал/i)).toBeInTheDocument())
  })
})
