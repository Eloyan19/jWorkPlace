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

describe('App — вкладка "Поддержка сервиса" отделена от проектных вкладок', () => {
  it('присутствует в общем tablist под обновлённым label', () => {
    render(<App />)
    const tablist = screen.getByRole('tablist', { name: 'разделы' })
    const supportTab = screen.getByRole('tab', { name: 'Поддержка сервиса' })
    expect(tablist).toContainElement(supportTab)
  })

  it('визуально отделена от проектных вкладок отдельным классом-разделителем', () => {
    render(<App />)
    const supportTab = screen.getByRole('tab', { name: 'Поддержка сервиса' })
    expect(supportTab.className).toMatch(/\btab-support\b/)
    expect(screen.getByRole('tab', { name: 'Чат' }).className).not.toMatch(/\btab-support\b/)
  })

  it('активируется кликом и сохраняется в localStorage', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: 'Поддержка сервиса' }))
    expect(screen.getByRole('tab', { name: 'Поддержка сервиса' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Чат' })).toHaveAttribute('aria-selected', 'false')
    expect(window.localStorage.getItem(KEY)).toBe('support')
  })

  it('восстанавливается из localStorage при монтировании', () => {
    window.localStorage.setItem(KEY, 'support')
    render(<App />)
    expect(screen.getByRole('tab', { name: 'Поддержка сервиса' })).toHaveAttribute('aria-selected', 'true')
  })
})
