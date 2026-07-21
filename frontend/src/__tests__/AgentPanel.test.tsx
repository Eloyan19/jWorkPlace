import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AgentPanel from '../components/AgentPanel'
import * as api from '../api'
import type { AgentRunResponse, Project } from '../types'

vi.mock('../api')

const mockedApi = vi.mocked(api)

function readyProject(): Project {
  return { id: 'abc123', url: 'u', name: 'repo', status: 'ready', error: null, indexed_at: null, can_edit: false }
}

function run(over: Partial<AgentRunResponse> = {}): AgentRunResponse {
  return { ok: true, needs_pr: false, result_text: 'striptags используется в src/util.py', sources: [], ...over }
}

describe('AgentPanel', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetAllMocks()
    localStorage.setItem('jwp_active_project', 'abc123')
    mockedApi.getProject.mockResolvedValue(readyProject())
  })
  afterEach(() => vi.restoreAllMocks())

  it('read-only задача: показывает текстовый итог без кнопки PR', async () => {
    mockedApi.runAgent.mockResolvedValue(run())
    render(<AgentPanel />)
    await waitFor(() => expect(screen.getByLabelText(/цель для агента/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/цель для агента/i), { target: { value: 'найди использования striptags' } })
    fireEvent.click(screen.getByRole('button', { name: /Запустить агента/i }))

    await waitFor(() => expect(screen.getByText(/используется в src\/util\.py/i)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /открыть PR/i })).not.toBeInTheDocument()
    expect(mockedApi.runAgent).toHaveBeenCalledWith('abc123', 'найди использования striptags')
  })

  it('«Уточнить» перезапускает агента с прошлой целью + поправкой', async () => {
    mockedApi.runAgent.mockResolvedValue(run())
    render(<AgentPanel />)
    await waitFor(() => expect(screen.getByLabelText(/цель для агента/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/цель для агента/i), { target: { value: 'сделай фон мягче' } })
    fireEvent.click(screen.getByRole('button', { name: /Запустить агента/i }))
    await waitFor(() => expect(screen.getByLabelText(/уточнение для агента/i)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/уточнение для агента/i), {
      target: { value: 'имелась в виду панель Структура' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Уточнить/i }))

    await waitFor(() =>
      expect(mockedApi.runAgent).toHaveBeenLastCalledWith(
        'abc123',
        'сделай фон мягче\n\nУточнение: имелась в виду панель Структура',
      ),
    )
  })

  it('задача-изменение с токеном: показывает diff и открывает PR', async () => {
    mockedApi.runAgent.mockResolvedValue(run({
      needs_pr: true, run_id: 'r1', can_edit: true, result_text: 'Создал CHANGELOG.md',
      diff: 'diff --git a/CHANGELOG.md b/CHANGELOG.md\n+# Changelog\n',
      sources: [{ file: 'CHANGELOG.md', reason: 'новый файл', citation: 'CHANGELOG.md' }],
    }))
    mockedApi.confirmAgentPr.mockResolvedValue({ status: 200, ok: true, pr_url: 'https://github.com/o/r/pull/9' })

    render(<AgentPanel />)
    await waitFor(() => expect(screen.getByLabelText(/цель для агента/i)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText(/цель для агента/i), { target: { value: 'сгенерируй changelog' } })
    fireEvent.click(screen.getByRole('button', { name: /Запустить агента/i }))

    await waitFor(() => expect(screen.getByText(/# Changelog/)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Подтвердить и открыть PR/i }))

    await waitFor(() => expect(screen.getByText('https://github.com/o/r/pull/9')).toBeInTheDocument())
    expect(mockedApi.confirmAgentPr).toHaveBeenCalledWith('abc123', 'r1')
  })

  it('задача-изменение без токена: подсказывает включить правки', async () => {
    mockedApi.runAgent.mockResolvedValue(run({
      needs_pr: true, run_id: 'r2', can_edit: false, result_text: 'готово',
      diff: 'diff --git a/docs/X.md b/docs/X.md\n+x\n', sources: [],
    }))
    render(<AgentPanel />)
    await waitFor(() => expect(screen.getByLabelText(/цель для агента/i)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText(/цель для агента/i), { target: { value: 'сделай доку' } })
    fireEvent.click(screen.getByRole('button', { name: /Запустить агента/i }))

    await waitFor(() => expect(screen.getByText(/включите правки токеном/i)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /открыть PR/i })).not.toBeInTheDocument()
  })

  it('устаревшее превью (409) при подтверждении', async () => {
    mockedApi.runAgent.mockResolvedValue(run({
      needs_pr: true, run_id: 'r3', can_edit: true, result_text: 'готово',
      diff: 'diff --git a/docs/X.md b/docs/X.md\n+x\n', sources: [],
    }))
    mockedApi.confirmAgentPr.mockResolvedValue({ status: 409, ok: false, reason: 'превью устарело' })
    render(<AgentPanel />)
    await waitFor(() => expect(screen.getByLabelText(/цель для агента/i)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText(/цель для агента/i), { target: { value: 'доку' } })
    fireEvent.click(screen.getByRole('button', { name: /Запустить агента/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /Подтвердить и открыть PR/i })).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Подтвердить и открыть PR/i }))
    await waitFor(() => expect(screen.getByText(/превью устарело — запустите агента заново/i)).toBeInTheDocument())
  })
})
