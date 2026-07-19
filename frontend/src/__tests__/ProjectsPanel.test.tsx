import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ProjectsPanel from '../components/ProjectsPanel'
import * as api from '../api'
import type { Project } from '../types'

vi.mock('../api')

const mockedApi = vi.mocked(api)

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    url: 'https://github.com/owner/repo',
    name: 'repo',
    status: 'ready',
    error: null,
    indexed_at: null,
    can_edit: false,
    ...overrides,
  }
}

describe('ProjectsPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('рендерит список проектов', async () => {
    mockedApi.listProjects.mockResolvedValue([
      makeProject({ id: 'p1', name: 'repo-one' }),
      makeProject({ id: 'p2', name: 'repo-two', status: 'indexing' }),
    ])

    render(<ProjectsPanel />)

    await waitFor(() => {
      expect(screen.getByText('repo-one')).toBeInTheDocument()
    })
    expect(screen.getByText('repo-two')).toBeInTheDocument()
  })

  it('кнопка "Подключить" вызывает createProject и обновляет список', async () => {
    mockedApi.listProjects.mockResolvedValueOnce([]).mockResolvedValueOnce([makeProject()])
    mockedApi.createProject.mockResolvedValue({ project_id: 'p1', status: 'cloning' })

    render(<ProjectsPanel />)

    await waitFor(() => expect(mockedApi.listProjects).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('ссылка на GitHub-репозиторий'), {
      target: { value: 'https://github.com/owner/repo' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Подключить/i }))

    await waitFor(() => {
      expect(mockedApi.createProject).toHaveBeenCalledWith('https://github.com/owner/repo')
    })
    await waitFor(() => {
      expect(screen.getByText('repo')).toBeInTheDocument()
    })
  })

  it('показывает бейджи статусов', async () => {
    mockedApi.listProjects.mockResolvedValue([
      makeProject({ id: 'p1', name: 'ready-one', status: 'ready' }),
      makeProject({ id: 'p2', name: 'error-one', status: 'error', error: 'что-то пошло не так' }),
    ])

    render(<ProjectsPanel />)

    await waitFor(() => {
      expect(screen.getByText('готов')).toBeInTheDocument()
    })
    expect(screen.getByText('ошибка')).toBeInTheDocument()
    expect(screen.getByText('что-то пошло не так')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Переиндексировать/i })).toBeInTheDocument()
  })

  it('клик по проекту делает его активным', async () => {
    mockedApi.listProjects.mockResolvedValue([
      makeProject({ id: 'p1', name: 'repo-one' }),
      makeProject({ id: 'p2', name: 'repo-two' }),
    ])

    render(<ProjectsPanel />)

    await waitFor(() => {
      expect(screen.getByText('repo-two')).toBeInTheDocument()
    })

    const secondSelectButton = screen.getByText('repo-two').closest('button')
    expect(secondSelectButton).not.toBeNull()
    fireEvent.click(secondSelectButton!)

    await waitFor(() => {
      expect(secondSelectButton).toHaveAttribute('aria-pressed', 'true')
    })
    expect(localStorage.getItem('jwp_active_project')).toBe('p2')
  })

  it('показывает read-only и форму токена для готового проекта без can_edit', async () => {
    mockedApi.listProjects.mockResolvedValue([makeProject({ can_edit: false })])

    render(<ProjectsPanel />)

    await waitFor(() => expect(screen.getByText('🔒 read-only')).toBeInTheDocument())
    expect(screen.getByLabelText('GitHub-токен для repo')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Отключить/i })).not.toBeInTheDocument()
  })

  it('показывает бейдж «правки включены» и кнопку «Отключить» при can_edit', async () => {
    mockedApi.listProjects.mockResolvedValue([makeProject({ can_edit: true })])

    render(<ProjectsPanel />)

    await waitFor(() => expect(screen.getByText('✅ правки включены')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Отключить/i })).toBeInTheDocument()
    expect(screen.queryByLabelText('GitHub-токен для repo')).not.toBeInTheDocument()
  })

  it('отправка токена вызывает putProjectToken и обновляет бейдж', async () => {
    mockedApi.listProjects
      .mockResolvedValueOnce([makeProject({ can_edit: false })])
      .mockResolvedValueOnce([makeProject({ can_edit: true })])
    mockedApi.putProjectToken.mockResolvedValue({ can_edit: true })

    render(<ProjectsPanel />)

    await waitFor(() => expect(screen.getByLabelText('GitHub-токен для repo')).toBeInTheDocument())

    const input = screen.getByLabelText('GitHub-токен для repo') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'github_pat_secret' } })
    fireEvent.click(screen.getByRole('button', { name: /Включить правки/i }))

    await waitFor(() => {
      expect(mockedApi.putProjectToken).toHaveBeenCalledWith('p1', 'github_pat_secret')
    })
    // Успех переключает бейдж на «правки включены» — форма токена (и введённое значение)
    // полностью уходит из DOM, токен нигде не остаётся.
    await waitFor(() => expect(screen.getByText('✅ правки включены')).toBeInTheDocument())
    expect(screen.queryByLabelText('GitHub-токен для repo')).not.toBeInTheDocument()
  })

  it('провал включения правок показывает ошибку и не хранит токен', async () => {
    mockedApi.listProjects.mockResolvedValue([makeProject({ can_edit: false })])
    mockedApi.putProjectToken.mockRejectedValue(new Error('токен не подходит'))

    render(<ProjectsPanel />)

    await waitFor(() => expect(screen.getByLabelText('GitHub-токен для repo')).toBeInTheDocument())

    const input = screen.getByLabelText('GitHub-токен для repo') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'bad-token' } })
    fireEvent.click(screen.getByRole('button', { name: /Включить правки/i }))

    await waitFor(() => expect(screen.getByText('токен не подходит')).toBeInTheDocument())
    expect(input.value).toBe('')
  })

  it('кнопка «Отключить» вызывает deleteProjectToken и возвращает read-only', async () => {
    mockedApi.listProjects
      .mockResolvedValueOnce([makeProject({ can_edit: true })])
      .mockResolvedValueOnce([makeProject({ can_edit: false })])
    mockedApi.deleteProjectToken.mockResolvedValue({ can_edit: false })

    render(<ProjectsPanel />)

    await waitFor(() => expect(screen.getByRole('button', { name: /Отключить/i })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Отключить/i }))

    await waitFor(() => {
      expect(mockedApi.deleteProjectToken).toHaveBeenCalledWith('p1')
    })
    await waitFor(() => expect(screen.getByText('🔒 read-only')).toBeInTheDocument())
  })
})
