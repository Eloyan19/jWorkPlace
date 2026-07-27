import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import PanelHeader from '../components/PanelHeader'

describe('PanelHeader', () => {
  it('renders title as h2 heading', () => {
    render(<PanelHeader title="Test Title" />)
    const heading = screen.getByRole('heading', { level: 2 })
    expect(heading).toHaveTextContent('Test Title')
  })

  it('renders subtitle paragraph when subtitle prop is provided', () => {
    render(<PanelHeader title="Test Title" subtitle="Test Subtitle" />)
    const subtitle = screen.getByText('Test Subtitle')
    expect(subtitle).toHaveClass('panel-subtitle')
    expect(subtitle.tagName).toBe('P')
  })

  it('does not render subtitle paragraph when subtitle prop is not provided', () => {
    const { container } = render(<PanelHeader title="Test Title" />)
    const subtitle = container.querySelector('.panel-subtitle')
    expect(subtitle).toBeNull()
  })
})
