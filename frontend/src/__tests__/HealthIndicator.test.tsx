import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HealthIndicator from '../components/HealthIndicator'

describe('HealthIndicator', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('показывает "online" и версию при успешном /api/health', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: 'ok', version: 'abc' }),
      }),
    )

    render(<HealthIndicator />)

    await waitFor(() => {
      expect(screen.getByText(/backend online/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/abc/)).toBeInTheDocument()
    expect(document.querySelector('.health-online')).toBeInTheDocument()
  })

  it('показывает "offline" при сетевой ошибке', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')))

    render(<HealthIndicator />)

    await waitFor(() => {
      expect(screen.getByText(/backend offline/i)).toBeInTheDocument()
    })
    expect(document.querySelector('.health-offline')).toBeInTheDocument()
  })

  it('показывает "offline" при ответе 500', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({}),
      }),
    )

    render(<HealthIndicator />)

    await waitFor(() => {
      expect(screen.getByText(/backend offline/i)).toBeInTheDocument()
    })
  })
})
