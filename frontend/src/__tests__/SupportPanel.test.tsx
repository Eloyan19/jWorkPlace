import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SupportPanel from '../components/SupportPanel'
import * as api from '../api'
import type { SupportResponse } from '../types'

vi.mock('../api')

const mockedApi = vi.mocked(api)

function answer(over: Partial<SupportResponse> = {}): SupportResponse {
  return {
    answer: 'Скорость индексации зависит от размера репозитория.',
    escalate: false,
    ticket_applied: false,
    sources: [{
      file: 'faq.md',
      section: 'Почему репозиторий долго индексируется',
      citation: 'faq.md::Почему репозиторий долго индексируется::L20-30',
      quote: 'Скорость индексации зависит от размера репозитория',
    }],
    ...over,
  }
}

describe('SupportPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('показывает ответ с источником FAQ', async () => {
    mockedApi.askSupport.mockResolvedValue(answer())
    render(<SupportPanel />)

    fireEvent.change(screen.getByLabelText(/вопрос в поддержку/i), {
      target: { value: 'почему долго индексируется' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    await waitFor(() =>
      expect(
        screen.getByText('Скорость индексации зависит от размера репозитория.'),
      ).toBeInTheDocument(),
    )
    expect(screen.getByText('Почему репозиторий долго индексируется')).toBeInTheDocument()
    expect(mockedApi.askSupport).toHaveBeenCalledWith('почему долго индексируется', '')
  })

  it('передаёт номер тикета и показывает пометку об учёте контекста', async () => {
    mockedApi.askSupport.mockResolvedValue(answer({ ticket_applied: true }))
    render(<SupportPanel />)

    fireEvent.change(screen.getByLabelText(/вопрос в поддержку/i), { target: { value: 'почему долго' } })
    fireEvent.change(screen.getByLabelText(/номер тикета/i), { target: { value: 'T-1001' } })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    await waitFor(() => expect(screen.getByText(/учтён контекст указанного тикета/i)).toBeInTheDocument())
    expect(mockedApi.askSupport).toHaveBeenCalledWith('почему долго', 'T-1001')
  })

  it('эскалация показывается без источников', async () => {
    mockedApi.askSupport.mockResolvedValue(
      answer({ escalate: true, answer: 'Передаю обращение специалисту.', sources: [] }),
    )
    render(<SupportPanel />)

    fireEvent.change(screen.getByLabelText(/вопрос в поддержку/i), { target: { value: 'рецепт борща' } })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    await waitFor(() => expect(screen.getByText(/Передаю обращение специалисту/i)).toBeInTheDocument())
  })

  it('показывает ошибку при сбое', async () => {
    mockedApi.askSupport.mockRejectedValue(new Error('backend упал'))
    render(<SupportPanel />)

    fireEvent.change(screen.getByLabelText(/вопрос в поддержку/i), { target: { value: 'вопрос' } })
    fireEvent.click(screen.getByRole('button', { name: /Спросить/i }))

    await waitFor(() => expect(screen.getByText(/backend упал/i)).toBeInTheDocument())
  })
})
