import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import * as api from '../api'
import type { Project } from '../types'

const KEY = 'jwp_active_tab'
const ACTIVE_PROJECT_KEY = 'jwp_active_project'

const READY_PROJECT: Project = {
  id: 'proj-1',
  url: 'https://github.com/example/repo',
  name: 'repo',
  status: 'ready',
  can_edit: false,
}

// Все tab-pane остаются смонтированными разом (hidden, не unmount) — чтобы не путать
// EmptyState активной вкладки с EmptyState скрытых, ищем текст только внутри видимой панели.
function visiblePane(): HTMLElement {
  const panes = document.querySelectorAll<HTMLElement>('.tab-pane')
  const visible = Array.from(panes).find((p) => !p.hasAttribute('hidden'))
  if (!visible) throw new Error('нет видимой tab-pane')
  return visible
}

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

describe('App — клавиатурная навигация по таб-бару (WAI-ARIA Tabs)', () => {
  it('roving tabindex: активная вкладка tabIndex=0, остальные -1', () => {
    render(<App />)
    expect(screen.getByRole('tab', { name: 'Чат' })).toHaveAttribute('tabIndex', '0')
    expect(screen.getByRole('tab', { name: 'Поиск' })).toHaveAttribute('tabIndex', '-1')
    expect(screen.getByRole('tab', { name: 'Поддержка сервиса' })).toHaveAttribute('tabIndex', '-1')
  })

  it('ArrowRight переключает на следующую вкладку и переносит фокус', () => {
    render(<App />)
    const chatTab = screen.getByRole('tab', { name: 'Чат' })
    chatTab.focus()
    fireEvent.keyDown(chatTab, { key: 'ArrowRight' })
    const summaryTab = screen.getByRole('tab', { name: 'О проекте' })
    expect(summaryTab).toHaveAttribute('aria-selected', 'true')
    expect(summaryTab).toHaveFocus()
    expect(window.localStorage.getItem(KEY)).toBe('summary')
  })

  it('ArrowLeft с первой вкладки переносит на последнюю (support) по кольцу', () => {
    render(<App />)
    const chatTab = screen.getByRole('tab', { name: 'Чат' })
    chatTab.focus()
    fireEvent.keyDown(chatTab, { key: 'ArrowLeft' })
    const supportTab = screen.getByRole('tab', { name: 'Поддержка сервиса' })
    expect(supportTab).toHaveAttribute('aria-selected', 'true')
    expect(supportTab).toHaveFocus()
  })

  it('ArrowRight с последней вкладки (support) переносит на первую по кольцу', () => {
    window.localStorage.setItem(KEY, 'support')
    render(<App />)
    const supportTab = screen.getByRole('tab', { name: 'Поддержка сервиса' })
    supportTab.focus()
    fireEvent.keyDown(supportTab, { key: 'ArrowRight' })
    const chatTab = screen.getByRole('tab', { name: 'Чат' })
    expect(chatTab).toHaveAttribute('aria-selected', 'true')
    expect(chatTab).toHaveFocus()
  })

  it('End переключает сразу на последнюю вкладку (support)', () => {
    render(<App />)
    const chatTab = screen.getByRole('tab', { name: 'Чат' })
    chatTab.focus()
    fireEvent.keyDown(chatTab, { key: 'End' })
    const supportTab = screen.getByRole('tab', { name: 'Поддержка сервиса' })
    expect(supportTab).toHaveAttribute('aria-selected', 'true')
    expect(supportTab).toHaveFocus()
  })

  it('Home из середины переключает на первую вкладку (Чат)', () => {
    window.localStorage.setItem(KEY, 'search')
    render(<App />)
    const searchTab = screen.getByRole('tab', { name: 'Поиск' })
    searchTab.focus()
    fireEvent.keyDown(searchTab, { key: 'Home' })
    const chatTab = screen.getByRole('tab', { name: 'Чат' })
    expect(chatTab).toHaveAttribute('aria-selected', 'true')
    expect(chatTab).toHaveFocus()
  })
})

describe('App — EmptyState на проектных вкладках без активного проекта', () => {
  it('без активного проекта вкладка "Чат" показывает единую подсказку вместо панели', () => {
    render(<App />)
    const pane = within(visiblePane())
    expect(pane.getByText('Проект не выбран')).toBeInTheDocument()
    expect(
      pane.getByText('Выберите проиндексированный проект в панели проектов выше, чтобы начать'),
    ).toBeInTheDocument()
    // Форма чата (специфичная для ChatPanel) не смонтирована — панель не рендерится вовсе.
    expect(pane.queryByPlaceholderText(/что делает проект/)).not.toBeInTheDocument()
  })

  it('без активного проекта другие проектные вкладки (Структура/Поиск/Правки) тоже показывают EmptyState', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: 'Структура' }))
    expect(within(visiblePane()).getByText('Проект не выбран')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Поиск' }))
    expect(within(visiblePane()).getByText('Проект не выбран')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Правки' }))
    expect(within(visiblePane()).getByText('Проект не выбран')).toBeInTheDocument()
  })

  it('вкладка "Поддержка сервиса" доступна и без активного проекта (не EmptyState)', () => {
    render(<App />)
    fireEvent.click(screen.getByRole('tab', { name: 'Поддержка сервиса' }))
    const pane = within(visiblePane())
    expect(pane.queryByText('Проект не выбран')).not.toBeInTheDocument()
    expect(pane.getByRole('heading', { name: 'Поддержка пользователей' })).toBeInTheDocument()
  })

  it('с активным проектом вкладка "Чат" рендерит саму панель, а не EmptyState', () => {
    vi.mocked(api.getProject).mockResolvedValue(READY_PROJECT)
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, READY_PROJECT.id)
    render(<App />)
    const pane = within(visiblePane())
    expect(pane.getByPlaceholderText(/что делает проект/)).toBeInTheDocument()
    expect(pane.queryByText('Проект не выбран')).not.toBeInTheDocument()
  })
})

describe('App — индикатор активного проекта над tab-pane (T09)', () => {
  // listProjects тоже должен видеть READY_PROJECT — иначе ProjectsPanel.refresh() решит, что
  // активный id пропал из списка, и сам вызовет clearActiveProject() (гонка, не связанная с
  // индикатором, но ломающая асинхронные проверки ниже). ProjectsPanel рендерит то же имя
  // "repo" в своём списке — поэтому текст индикатора ищем через querySelector по его классу,
  // а не screen.findByText (иначе "Found multiple elements").
  beforeEach(() => {
    vi.mocked(api.listProjects).mockResolvedValue([READY_PROJECT])
  })

  function activeProjectBarText(): string | null {
    return document.querySelector('.active-project-name')?.textContent ?? null
  }

  it('с активным проектом на проектной вкладке показывает его имя', async () => {
    vi.mocked(api.getProject).mockResolvedValue(READY_PROJECT)
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, READY_PROJECT.id)
    render(<App />)
    await vi.waitFor(() => expect(activeProjectBarText()).toBe(READY_PROJECT.name))
    expect(document.querySelector('.active-project-bar')).toBeInTheDocument()
  })

  it('без активного проекта индикатор не рендерится', () => {
    render(<App />)
    expect(document.querySelector('.active-project-bar')).not.toBeInTheDocument()
  })

  it('на вкладке "Поддержка сервиса" индикатор скрыт даже при активном проекте', async () => {
    vi.mocked(api.getProject).mockResolvedValue(READY_PROJECT)
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, READY_PROJECT.id)
    render(<App />)
    await vi.waitFor(() => expect(activeProjectBarText()).toBe(READY_PROJECT.name))
    fireEvent.click(screen.getByRole('tab', { name: 'Поддержка сервиса' }))
    expect(document.querySelector('.active-project-bar')).not.toBeInTheDocument()
  })

  it('при ошибке getProject индикатор не падает и показывает id', async () => {
    vi.mocked(api.getProject).mockRejectedValue(new Error('boom'))
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, READY_PROJECT.id)
    render(<App />)
    await vi.waitFor(() => expect(activeProjectBarText()).toBe(READY_PROJECT.id))
  })
})

describe('App — структура таб-бара (T10)', () => {
  it('рендерит ровно 6 вкладок в одном tablist', () => {
    render(<App />)
    const tablist = screen.getByRole('tablist', { name: 'разделы' })
    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(6)
    expect(tablist).toContainElement(tabs[0])
    expect(tablist).toContainElement(tabs[5])
  })

  it('вкладки расположены в правильном порядке', () => {
    render(<App />)
    const tabs = screen.getAllByRole('tab')
    const labels = tabs.map((t) => t.textContent)
    expect(labels).toEqual(['Чат', 'О проекте', 'Структура', 'Поиск', 'Правки', 'Поддержка сервиса'])
  })

  it('только последняя вкладка имеет класс tab-support', () => {
    render(<App />)
    const tabs = screen.getAllByRole('tab')
    for (let i = 0; i < tabs.length - 1; i++) {
      expect(tabs[i].className).not.toMatch(/\btab-support\b/)
    }
    expect(tabs[5].className).toMatch(/\btab-support\b/)
  })
})
