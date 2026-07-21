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

  it('по кнопке грузит дерево: папка и файл видны, символы — по клику', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getStructure.mockResolvedValue(structure())

    render(<StructurePanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Показать структуру/i })).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: /Показать структуру/i }))

    // Дерево: папка `src` раскрыта по умолчанию, файл `util.py` виден, символы скрыты.
    await waitFor(() => expect(screen.getByText('src')).toBeInTheDocument())
    expect(screen.getByText('util.py')).toBeInTheDocument()
    expect(screen.getByText(/1 файлов · 2 символов/i)).toBeInTheDocument()
    expect(screen.queryByText('foo')).not.toBeInTheDocument()

    // Клик по файлу раскрывает его символы.
    fireEvent.click(screen.getByText('util.py'))
    await waitFor(() => expect(screen.getByText('foo')).toBeInTheDocument())
    expect(screen.getByText('Bar')).toBeInTheDocument()
    expect(mockedApi.getStructure).toHaveBeenCalledWith('abc123')
  })

  it('схлопывает одиночные цепочки папок в одну строку (compact folders)', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getStructure.mockResolvedValue({
      project_id: 'abc123', name: 'repo', file_count: 2, symbol_count: 0,
      files: [
        { path: 'backend/app/api/agent.py', lang: 'python', size: 10, excluded: false, symbols: [] },
        { path: 'backend/app/api/chat.py', lang: 'python', size: 10, excluded: false, symbols: [] },
      ],
    })

    render(<StructurePanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Показать структуру/i })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Показать структуру/i }))

    // Цепочка backend→app→api схлопнута в одну строку; отдельных строк «backend»/«app» нет.
    await waitFor(() => expect(screen.getByText('backend/app/api')).toBeInTheDocument())
    expect(screen.queryByText('backend')).not.toBeInTheDocument()
    expect(screen.queryByText('app')).not.toBeInTheDocument()
    // Файлы точки ветвления видны (папка раскрыта по умолчанию).
    expect(screen.getByText('agent.py')).toBeInTheDocument()
    expect(screen.getByText('chat.py')).toBeInTheDocument()
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
