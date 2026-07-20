import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ChatPanel from '../components/ChatPanel'
import * as api from '../api'
import { writeActiveProject } from '../activeProject'
import type { ChatResponse, Project } from '../types'

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

function chatResponse(over: Partial<ChatResponse> = {}): ChatResponse {
  return {
    answer: 'Класс Markup экранирует HTML.',
    abstain: false,
    sources: [
      {
        id: 1,
        file: 'src/markupsafe/_native.py',
        symbol: 'Markup',
        lines: 'L10-20',
        citation: 'src/markupsafe/_native.py::Markup::L10-20',
        quote: 'class Markup(str): ...',
      },
    ],
    ...over,
  }
}

describe('ChatPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('без активного проекта показывает подсказку', () => {
    render(<ChatPanel />)
    expect(screen.getByText(/выберите готовый проект/i)).toBeInTheDocument()
  })

  it('для не-готового проекта показывает, что он индексируется', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue({ ...readyProject(), status: 'indexing' })
    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByText(/ещё индексируется/i)).toBeInTheDocument())
  })

  it('рендерит ответ с источниками', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.sendChat.mockResolvedValue(chatResponse())

    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByLabelText(/вопрос по коду/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/вопрос по коду/i), {
      target: { value: 'что делает класс Markup' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    expect(screen.getByText('что делает класс Markup')).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.getByText('Класс Markup экранирует HTML.')).toBeInTheDocument()
    })
    expect(screen.getByText('src/markupsafe/_native.py::Markup::L10-20')).toBeInTheDocument()
    expect(screen.getByText(/class Markup/)).toBeInTheDocument()
    expect(mockedApi.sendChat).toHaveBeenCalledWith('abc123', [
      { role: 'user', content: 'что делает класс Markup' },
    ])
  })

  it('показывает «не знаю» при abstain, без источников', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.sendChat.mockResolvedValue(
      chatResponse({ answer: 'не знаю, уточните вопрос.', abstain: true, sources: [] }),
    )

    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByLabelText(/вопрос по коду/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/вопрос по коду/i), { target: { value: 'какая погода' } })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    await waitFor(() => {
      expect(screen.getByText(/не знаю, уточните/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/::L/)).not.toBeInTheDocument()
  })

  it('/help отвечает статически, не вызывая backend', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())

    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByLabelText(/вопрос по коду/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/вопрос по коду/i), { target: { value: '/help' } })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    await waitFor(() => expect(screen.getByText(/Я — ассистент по этому проекту/i)).toBeInTheDocument())
    expect(screen.getByText(/показать структуру проекта/i)).toBeInTheDocument()
    expect(mockedApi.sendChat).not.toHaveBeenCalled()
  })

  it('показывает ошибку при сбое чата', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.sendChat.mockRejectedValue(new Error('backend упал'))

    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByLabelText(/вопрос по коду/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/вопрос по коду/i), { target: { value: 'что делает проект' } })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    await waitFor(() => expect(screen.getByText(/backend упал/i)).toBeInTheDocument())
  })

  it('переключение проекта сбрасывает диалог', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.sendChat.mockResolvedValue(chatResponse())

    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByLabelText(/вопрос по коду/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/вопрос по коду/i), {
      target: { value: 'что делает класс Markup' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    await waitFor(() => {
      expect(screen.getByText('Класс Markup экранирует HTML.')).toBeInTheDocument()
    })

    mockedApi.getProject.mockResolvedValue({ ...readyProject(), id: 'other456', name: 'другой репо' })
    writeActiveProject('other456')

    await waitFor(() => {
      expect(screen.queryByText('Класс Markup экранирует HTML.')).not.toBeInTheDocument()
      expect(screen.queryByText('что делает класс Markup')).not.toBeInTheDocument()
    })
  })
})
