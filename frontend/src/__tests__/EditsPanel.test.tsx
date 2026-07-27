import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import EditsPanel from '../components/EditsPanel'

// EditPanel/AgentPanel рендерятся оба разом (hidden, не unmount) — без активного проекта в
// localStorage они не зовут getProject, но мокаем api целиком по конвенции модуля (см. App.test.tsx).
vi.mock('../api')

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('EditsPanel — сегмент-переключатель под-режимов', () => {
  it('по умолчанию активна «Быстрая правка», её панель видна, «Агент по файлам» скрыт', () => {
    render(<EditsPanel />)

    const quickTab = screen.getByRole('tab', { name: 'Быстрая правка' })
    const agentTab = screen.getByRole('tab', { name: 'Агент по файлам' })
    expect(quickTab).toHaveAttribute('aria-selected', 'true')
    expect(agentTab).toHaveAttribute('aria-selected', 'false')

    expect(screen.getByText('Правка кода')).toBeVisible()
    expect(screen.getByText('Агент по файлам', { selector: 'h2' })).not.toBeVisible()
  })

  it('клик по «Агент по файлам» переключает видимость панелей', () => {
    render(<EditsPanel />)

    fireEvent.click(screen.getByRole('tab', { name: 'Агент по файлам' }))

    expect(screen.getByRole('tab', { name: 'Агент по файлам' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(screen.getByRole('tab', { name: 'Быстрая правка' })).toHaveAttribute(
      'aria-selected',
      'false',
    )
    expect(screen.getByText('Агент по файлам', { selector: 'h2' })).toBeVisible()
    expect(screen.getByText('Правка кода')).not.toBeVisible()
  })

  it('под сегментами показаны пояснения когда использовать каждый режим', () => {
    render(<EditsPanel />)

    expect(
      screen.getByText(/быстрый одношаговый патч по известному месту/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/сам исследует проект и создаёт файлы/i),
    ).toBeInTheDocument()
  })
})
