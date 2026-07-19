import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import EditPanel from '../components/EditPanel'
import * as api from '../api'
import { writeActiveProject } from '../activeProject'
import type { EditResponse, Project } from '../types'

vi.mock('../api')

const mockedApi = vi.mocked(api)

function readyProject(): Project {
  return { id: 'abc123', url: 'u', name: 'repo', status: 'ready', error: null, indexed_at: null }
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

  it('рендерит diff с подсветкой и disabled-кнопку PR', async () => {
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

    const prButton = screen.getByRole('button', { name: /Подтвердить и открыть PR/i })
    expect(prButton).toBeDisabled()
    expect(prButton).toHaveAttribute('title', 'появится на Этапе 3b')
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
