import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import EditPanel from '../components/EditPanel'
import * as api from '../api'
import { writeActiveProject } from '../activeProject'
import type { EditResponse, Project } from '../types'

vi.mock('../api')

const mockedApi = vi.mocked(api)

function readyProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'abc123',
    url: 'u',
    name: 'repo',
    status: 'ready',
    error: null,
    indexed_at: null,
    can_edit: false,
    ...overrides,
  }
}

function okEditResponse(): EditResponse {
  return {
    ok: true,
    summary: 'Добавлена проверка пустой строки.',
    diff:
      '--- a/src/util.py\n' +
      '+++ b/src/util.py\n' +
      '@@ -1,3 +1,4 @@\n' +
      ' def foo():\n' +
      '-    return None\n' +
      '+    if not x:\n' +
      '+        return None\n',
    edits: [{ file: 'src/util.py', reason: 'добавлена проверка' }],
    sources: [
      {
        file: 'src/util.py',
        citation: 'src/util.py::foo::L1-3',
        quote: 'def foo():\n    return None',
      },
    ],
    dropped: 0,
  }
}

describe('EditPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('без активного проекта показывает подсказку', () => {
    render(<EditPanel />)
    expect(screen.getByText(/выберите готовый проект/i)).toBeInTheDocument()
  })

  it('для не-готового проекта показывает, что он индексируется', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue({ ...readyProject(), status: 'indexing' })
    render(<EditPanel />)
    await waitFor(() => expect(screen.getByText(/ещё индексируется/i)).toBeInTheDocument())
  })

  it('рендерит diff с подсветкой и подсказку про токен вместо PR-кнопки при can_edit=false', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.proposeEdit.mockResolvedValue(okEditResponse())

    render(<EditPanel />)
    await waitFor(() => expect(screen.getByLabelText(/описание правки/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/описание правки/i), {
      target: { value: 'добавь проверку пустой строки в foo' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Предложить правку/i }))

    await waitFor(() => {
      expect(screen.getByText('Добавлена проверка пустой строки.')).toBeInTheDocument()
    })

    expect(mockedApi.proposeEdit).toHaveBeenCalledWith('abc123', 'добавь проверку пустой строки в foo')

    const preserveWhitespace = { normalizer: (s: string) => s }
    const added = screen.getByText('+    if not x:', preserveWhitespace)
    expect(added).toHaveClass('diff-add')
    const removed = screen.getByText('-    return None', preserveWhitespace)
    expect(removed).toHaveClass('diff-del')
    const meta = screen.getByText('--- a/src/util.py', preserveWhitespace)
    expect(meta).toHaveClass('diff-meta')

    expect(screen.getByText('src/util.py::foo::L1-3')).toBeInTheDocument()

    expect(screen.queryByRole('button', { name: /Подтвердить и открыть PR/i })).not.toBeInTheDocument()
    expect(screen.getByText(/включите правки токеном проекта/i)).toBeInTheDocument()
  })

  it('кнопка PR активна при can_edit=true и открывает PR по клику', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject({ can_edit: true }))
    mockedApi.proposeEdit.mockResolvedValue(okEditResponse())
    mockedApi.createPr.mockResolvedValue({
      status: 200,
      ok: true,
      pr_url: 'https://github.com/owner/repo/pull/7',
    })

    render(<EditPanel />)
    await waitFor(() => expect(screen.getByLabelText(/описание правки/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/описание правки/i), {
      target: { value: 'добавь проверку пустой строки в foo' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Предложить правку/i }))

    const prButton = await screen.findByRole('button', { name: /Подтвердить и открыть PR/i })
    expect(prButton).not.toBeDisabled()

    fireEvent.click(prButton)

    const expectedDiff = okEditResponse()
    if (!expectedDiff.ok) throw new Error('unreachable: okEditResponse is always ok:true')
    await waitFor(() => {
      expect(mockedApi.createPr).toHaveBeenCalledWith('abc123', {
        instruction: 'добавь проверку пустой строки в foo',
        expected_diff: expectedDiff.diff,
      })
    })

    const link = await screen.findByRole('link', { name: /https:\/\/github\.com\/owner\/repo\/pull\/7/i })
    expect(link).toHaveAttribute('href', 'https://github.com/owner/repo/pull/7')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('409 от /pr показывает «превью устарело» и не даёт повторный клик', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject({ can_edit: true }))
    mockedApi.proposeEdit.mockResolvedValue(okEditResponse())
    mockedApi.createPr.mockResolvedValue({
      status: 409,
      ok: false,
      reason: 'превью устарело, обновите',
    })

    render(<EditPanel />)
    await waitFor(() => expect(screen.getByLabelText(/описание правки/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/описание правки/i), {
      target: { value: 'добавь проверку пустой строки в foo' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Предложить правку/i }))

    const prButton = await screen.findByRole('button', { name: /Подтвердить и открыть PR/i })
    fireEvent.click(prButton)

    await waitFor(() => {
      expect(screen.getByText(/превью устарело — сгенерируйте правку заново/i)).toBeInTheDocument()
    })
    expect(mockedApi.createPr).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: /Подтвердить и открыть PR/i })).not.toBeInTheDocument()
  })

  it('показывает «не могу выполнить правку» при ok:false', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.proposeEdit.mockResolvedValue({ ok: false, reason: 'не по коду этого проекта' })

    render(<EditPanel />)
    await waitFor(() => expect(screen.getByLabelText(/описание правки/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/описание правки/i), { target: { value: 'какая погода' } })
    fireEvent.click(screen.getByRole('button', { name: /Предложить правку/i }))

    await waitFor(() => {
      expect(screen.getByText(/не могу выполнить правку: не по коду этого проекта/i)).toBeInTheDocument()
    })
  })

  it('показывает ошибку при сбое сети', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.proposeEdit.mockRejectedValue(new Error('backend упал'))

    render(<EditPanel />)
    await waitFor(() => expect(screen.getByLabelText(/описание правки/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/описание правки/i), { target: { value: 'почини баг' } })
    fireEvent.click(screen.getByRole('button', { name: /Предложить правку/i }))

    await waitFor(() => expect(screen.getByText(/backend упал/i)).toBeInTheDocument())
  })

  it('переключение проекта сбрасывает предпросмотр', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.proposeEdit.mockResolvedValue(okEditResponse())

    render(<EditPanel />)
    await waitFor(() => expect(screen.getByLabelText(/описание правки/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/описание правки/i), {
      target: { value: 'добавь проверку пустой строки в foo' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Предложить правку/i }))

    await waitFor(() => {
      expect(screen.getByText('Добавлена проверка пустой строки.')).toBeInTheDocument()
    })

    mockedApi.getProject.mockResolvedValue({ ...readyProject(), id: 'other456', name: 'другой репо' })
    writeActiveProject('other456')

    await waitFor(() => {
      expect(screen.queryByText('Добавлена проверка пустой строки.')).not.toBeInTheDocument()
    })
  })
})
