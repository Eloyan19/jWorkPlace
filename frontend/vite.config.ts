/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Только для dev-сервера: nginx делает то же самое в проде (/api/* -> :8200).
      // Фронт всегда зовёт относительный /api/..., абсолютный backend-URL нигде не хардкодим.
      '/api': {
        target: 'http://127.0.0.1:8200',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/setupTests.ts'],
    // vitest — только unit-тесты в src/. E2E (frontend/e2e/*.spec.ts) гоняет Playwright,
    // его API несовместимо с vitest — иначе vitest подхватит smoke.spec.ts по глобу *.spec.ts.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
