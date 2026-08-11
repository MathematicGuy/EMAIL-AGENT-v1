import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'
import os from 'node:os'

// Dynamic hardware-adaptive concurrency:
// Uses 50% of available CPU threads (min 1, max 6) to prevent JSDOM memory exhaustion
// on low-RAM machines while scaling automatically on powerful workstations.
const systemCpus = os.cpus()?.length || 2
const calculatedWorkers = Math.max(1, Math.min(Math.floor(systemCpus / 2), 6))
const maxWorkers = process.env.VITEST_MAX_WORKERS
  ? Number.parseInt(process.env.VITEST_MAX_WORKERS, 10)
  : calculatedWorkers

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    maxWorkers,
  },
})

