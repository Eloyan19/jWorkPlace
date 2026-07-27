import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SummaryPanel from '../components/SummaryPanel'
import * as api from '../api'
import type { Project, ProjectSummary } from '../types'

vi.mock('../api')

const mockedApi = vi.mocked(api)

function readyProject(): Project {
  return {
    id: 'abc123', url: 'u', name: 'repo', status: 'ready',
    error: null, indexed_at: null, can_edit: false,
  }
}

function readySummary(): ProjectSummary {
  return {
    status: 'ready',
    overview: 'Сервис индексирует репозитории и отвечает по коду.',
    tech: ['FastAPI', 'FAISS'],
    concepts: {
      new: [
        {
          slug: 'hybrid-search',
          name: 'Hybrid search',
          detail: 'Комбинирует лексический и dense поиск через RRF.',
          evidence: [{ citation: 'backend/app/indexing/hybrid.py::hybrid_search::10-40', quote: 'def hybrid_search(' }],
        },
      ],
      known: [{ name: 'FastAPI роутеры' }],
    },
  }
}

describe('SummaryPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  // Заглушка «нет активного проекта» переехала на уровень App.tsx (EmptyState, T08) — App вообще
  // не монтирует SummaryPanel без активного проекта, см. App.test.tsx.

  it('показывает подзаголовок назначения панели', () => {
    render(<SummaryPanel active />)
    expect(screen.getByText(/выжимка о репозитории/i)).toBeInTheDocument()
  })

  it('не грузит ничего, пока вкладка не активна', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getSummary.mockResolvedValue(readySummary())

    render(<SummaryPanel active={false} />)

    await new Promise((r) => setTimeout(r, 0))
    expect(mockedApi.getProject).not.toHaveBeenCalled()
    expect(mockedApi.getSummary).not.toHaveBeenCalled()
  })

  it('рендерит new подробно (раскрываемо) и known — только именем; НЕ зовёт авто-пометку', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getSummary.mockResolvedValue(readySummary())

    render(<SummaryPanel active />)

    await waitFor(() => expect(screen.getByText(/Сервис индексирует репозитории/)).toBeInTheDocument())
    expect(screen.getByText('FastAPI')).toBeInTheDocument()
    expect(screen.getByText('Hybrid search')).toBeInTheDocument()
    expect(screen.getByText('FastAPI роутеры')).toBeInTheDocument()

    // Деталь нового концепта скрыта, пока не раскрыли.
    expect(screen.queryByText(/Комбинирует лексический/)).not.toBeInTheDocument()
    screen.getByRole('button', { name: /Hybrid search/i }).click()
    await waitFor(() => expect(screen.getByText(/Комбинирует лексический/)).toBeInTheDocument())
    expect(screen.getByText(/hybrid.py::hybrid_search::10-40/)).toBeInTheDocument()

    // Никакой авто-пометки — открытие/раскрытие концепта не зовёт backend.
    await new Promise((r) => setTimeout(r, 0))
    expect(mockedApi.markConceptKnown).not.toHaveBeenCalled()
  })

  it('кнопка «Изучено» зовёт markConceptKnown и переносит концепт в «Уже знакомо»', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getSummary.mockResolvedValue(readySummary())
    mockedApi.markConceptKnown.mockResolvedValue({ ok: true })

    render(<SummaryPanel active />)

    await waitFor(() => expect(screen.getByText('Hybrid search')).toBeInTheDocument())
    screen.getByRole('button', { name: 'Изучено' }).click()

    await waitFor(() => expect(mockedApi.markConceptKnown).toHaveBeenCalledWith('hybrid-search'))
    // концепт исчез из «Новое для вас» и появился в «Уже знакомо»
    await waitFor(() => expect(screen.queryByRole('button', { name: /Hybrid search/i })).not.toBeInTheDocument())
    const knownList = screen.getByText('FastAPI роутеры').closest('ul')
    expect(knownList).not.toBeNull()
    expect(knownList!.textContent).toContain('Hybrid search')
  })

  it('кнопка «Очистить базу знаний» после подтверждения зовёт resetKnownConcepts и перезагружает summary', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getSummary.mockResolvedValue(readySummary())
    mockedApi.resetKnownConcepts.mockResolvedValue({ ok: true, reset: 3 })

    render(<SummaryPanel active />)

    await waitFor(() => expect(screen.getByText('Очистить базу знаний')).toBeInTheDocument())
    screen.getByRole('button', { name: 'Очистить базу знаний' }).click()

    await waitFor(() => expect(mockedApi.resetKnownConcepts).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(mockedApi.getSummary).toHaveBeenCalledTimes(2))
  })

  it('отмена подтверждения не зовёт resetKnownConcepts', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getSummary.mockResolvedValue(readySummary())

    render(<SummaryPanel active />)

    await waitFor(() => expect(screen.getByText('Очистить базу знаний')).toBeInTheDocument())
    screen.getByRole('button', { name: 'Очистить базу знаний' }).click()

    await new Promise((r) => setTimeout(r, 0))
    expect(mockedApi.resetKnownConcepts).not.toHaveBeenCalled()
  })

  it('поллит пока backend отдаёт generating, затем показывает ready', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getSummary
      .mockResolvedValueOnce({ status: 'generating' })
      .mockResolvedValueOnce({ status: 'generating' })
      .mockResolvedValueOnce(readySummary())

    render(<SummaryPanel active />)

    await waitFor(() => expect(screen.getByText(/формируем выжимку/i)).toBeInTheDocument())
    expect(mockedApi.getSummary).toHaveBeenCalledTimes(1)

    // Реальные интервалы поллинга (2000мс) — ждём, пока третий ответ (ready) дойдёт до рендера.
    await waitFor(() => expect(mockedApi.getSummary).toHaveBeenCalledTimes(3), { timeout: 8_000 })
    await waitFor(() => expect(screen.getByText(/Сервис индексирует репозитории/)).toBeInTheDocument())
  }, 10_000)

  it('показывает ошибку генерации из status:"error"', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
    mockedApi.getSummary.mockResolvedValue({ status: 'error', reason: 'генерация не удалась' })

    render(<SummaryPanel active />)
    await waitFor(() => expect(screen.getByText(/генерация не удалась/i)).toBeInTheDocument())
  })

  it('проект ещё индексируется — не грузит выжимку', async () => {
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue({ ...readyProject(), status: 'indexing' })

    render(<SummaryPanel active />)
    await waitFor(() => expect(screen.getByText(/ещё индексируется/i)).toBeInTheDocument())
    expect(mockedApi.getSummary).not.toHaveBeenCalled()
  })
})
