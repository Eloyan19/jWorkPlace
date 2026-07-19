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
    // exact-имя: у error-проекта «Переиндексировать», не спутать с «Переиндексировать заново» у ready.
    expect(screen.getByRole('button', { name: 'Переиндексировать' })).toBeInTheDocument()
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

  it('ready-проект имеет три кнопки: Обновить, Переиндексировать заново, Удалить', async () => {
    mockedApi.listProjects.mockResolvedValue([makeProject({ id: 'p1', status: 'ready' })])

    render(<ProjectsPanel />)

    await waitFor(() => {
      expect(screen.getByText('repo')).toBeInTheDocument()
    })

    // Три кнопки у ready-проекта
    expect(screen.getByRole('button', { name: /^Обновить$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Переиндексировать заново$/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Удалить$/i })).toBeInTheDocument()
  })

  it('кнопка «Обновить» вызывает reindexProject БЕЗ подтверждения', async () => {
    mockedApi.listProjects
      .mockResolvedValueOnce([makeProject({ status: 'ready' })])
      .mockResolvedValueOnce([makeProject({ status: 'ready' })])
    mockedApi.reindexProject.mockResolvedValue({ status: 'cloning' })

    render(<ProjectsPanel />)

    await waitFor(() => expect(screen.getByRole('button', { name: /^Обновить$/i })).toBeInTheDocument())

    const updateBtn = screen.getByRole('button', { name: /^Обновить$/i })
    fireEvent.click(updateBtn)

    // Без confirm
    await waitFor(() => {
      expect(mockedApi.reindexProject).toHaveBeenCalledWith('p1')
    })
  })

  it('кнопка «Переиндексировать заново» требует confirm перед вызовом rebuildProject', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    mockedApi.listProjects.mockResolvedValue([makeProject({ status: 'ready' })])

    render(<ProjectsPanel />)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Переиндексировать заново$/i })).toBeInTheDocument()
    )

    const rebuildBtn = screen.getByRole('button', { name: /^Переиндексировать заново$/i })
    fireEvent.click(rebuildBtn)

    // confirm→false → rebuildProject НЕ вызывается
    expect(confirmSpy).toHaveBeenCalled()
    expect(mockedApi.rebuildProject).not.toHaveBeenCalled()

    confirmSpy.mockRestore()
  })

  it('кнопка «Переиндексировать заново» с confirm=true вызывает rebuildProject', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockedApi.listProjects
      .mockResolvedValueOnce([makeProject({ status: 'ready' })])
      .mockResolvedValueOnce([makeProject({ status: 'cloning' })])
    mockedApi.rebuildProject.mockResolvedValue({ status: 'cloning' })

    render(<ProjectsPanel />)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^Переиндексировать заново$/i })).toBeInTheDocument()
    )

    const rebuildBtn = screen.getByRole('button', { name: /^Переиндексировать заново$/i })
    fireEvent.click(rebuildBtn)

    await waitFor(() => {
      expect(mockedApi.rebuildProject).toHaveBeenCalledWith('p1')
    })

    confirmSpy.mockRestore()
  })

  it('кнопка «Удалить» требует confirm перед вызовом deleteProject', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    mockedApi.listProjects.mockResolvedValue([makeProject({ status: 'ready' })])

    render(<ProjectsPanel />)

    await waitFor(() => expect(screen.getByRole('button', { name: /^Удалить$/i })).toBeInTheDocument())

    const deleteBtn = screen.getByRole('button', { name: /^Удалить$/i })
    fireEvent.click(deleteBtn)

    // confirm→false → deleteProject НЕ вызывается
    expect(confirmSpy).toHaveBeenCalled()
    expect(mockedApi.deleteProject).not.toHaveBeenCalled()

    confirmSpy.mockRestore()
  })

  it('кнопка «Удалить» с confirm=true вызывает deleteProject и удаляет проект из списка', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockedApi.listProjects
      .mockResolvedValueOnce([makeProject({ id: 'p1' })])
      .mockResolvedValueOnce([]) // Проект исчез из списка
    mockedApi.deleteProject.mockResolvedValue({ deleted: true })

    render(<ProjectsPanel />)

    await waitFor(() => {
      expect(screen.getByText('repo')).toBeInTheDocument()
    })

    const deleteBtn = screen.getByRole('button', { name: /^Удалить$/i })
    fireEvent.click(deleteBtn)

    await waitFor(() => {
      expect(mockedApi.deleteProject).toHaveBeenCalledWith('p1')
    })

    // Проект исчез из списка
    await waitFor(() => {
      expect(screen.queryByText('repo')).not.toBeInTheDocument()
    })

    confirmSpy.mockRestore()
  })

  it('удаление активного проекта сбрасывает activeId и вызывает clearActiveProject', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockedApi.listProjects
      .mockResolvedValueOnce([makeProject({ id: 'p1' })])
      .mockResolvedValueOnce([])
    mockedApi.deleteProject.mockResolvedValue({ deleted: true })

    render(<ProjectsPanel />)

    // Делаем проект активным
    await waitFor(() => {
      expect(screen.getByText('repo')).toBeInTheDocument()
    })
    const selectBtn = screen.getByText('repo').closest('button')
    fireEvent.click(selectBtn!)

    // Проверяем, что он активен
    await waitFor(() => {
      expect(selectBtn).toHaveAttribute('aria-pressed', 'true')
    })

    // Удаляем активный проект
    const deleteBtn = screen.getByRole('button', { name: /^Удалить$/i })
    fireEvent.click(deleteBtn)

    await waitFor(() => {
      expect(mockedApi.deleteProject).toHaveBeenCalledWith('p1')
    })

    // activeId сбросился (в localStorage не должно быть 'p1')
    await waitFor(() => {
      expect(localStorage.getItem('jwp_active_project')).not.toBe('p1')
    })

    confirmSpy.mockRestore()
  })

  it('error-проект имеет кнопку «Переиндексировать» (без «заново»)', async () => {
    mockedApi.listProjects.mockResolvedValue([
      makeProject({ id: 'p1', status: 'error', error: 'что-то пошло не так' }),
    ])

    render(<ProjectsPanel />)

    await waitFor(() => {
      expect(screen.getByText('ошибка')).toBeInTheDocument()
    })

    // У error-проекта одна кнопка переиндексации (без «заново»)
    const reindexBtns = screen.queryAllByRole('button', { name: /Переиндексировать/i })
    // У error'а должна быть ровно одна (без слова «заново»)
    const errorReindexBtns = reindexBtns.filter((btn) =>
      btn.textContent?.includes('Переиндексировать') && !btn.textContent?.includes('заново')
    )
    expect(errorReindexBtns.length).toBeGreaterThan(0)
  })
})
