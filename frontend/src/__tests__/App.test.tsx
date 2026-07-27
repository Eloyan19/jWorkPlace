import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import * as api from '../api'

const KEY = 'jwp_active_tab'

// App монтирует все панели разом (табы не размонтируются) — им всем нужен минимальный ответ
// api.ts, иначе они улетают в error-состояние и засоряют вывод теста необработанными rejection.
vi.mock('../api')

beforeEach(() => {
  vi.mocked(api.getHealth).mockResolvedValue({ status: 'ok', version: 'test' })
  vi.mocked(api.listProjects).mockResolvedValue([])
  window.localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('App — персистентность активной вкладки', () => {
  it('без сохранённого значения открывает вкладку "Чат" по умолчанию', () => {
    render(<App />)
    expect(screen.getByRole('tab', { name: 'Чат' })).toHaveAttribute('aria-selected', 'true')
  })

  it('восстанавливает сохранённую вкладку при монтировании', () => {
    window.localStorage.setItem(KEY, 'search')
    render(<App />)
    expect(screen.getByRole('tab', { name: 'Поиск' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Чат' })).toHaveAttribute('aria-selected', 'false')
  })

  it('при переключении вкладки пишет выбор в localStorage', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: 'Структура' }))
    expect(screen.getByRole('tab', { name: 'Структура' })).toHaveAttribute('aria-selected', 'true')
    expect(window.localStorage.getItem(KEY)).toBe('structure')
  })

  it('при мусоре в localStorage откатывается на "Чат"', () => {
    window.localStorage.setItem(KEY, 'not-a-real-tab')
    render(<App />)
    expect(screen.getByRole('tab', { name: 'Чат' })).toHaveAttribute('aria-selected', 'true')
  })
})
