import { defineConfig, devices } from '@playwright/test';

/**
 * Frontend lives at Vite :5173. Chat API calls go through `/backend`.
 * The chat-latency project mocks those calls unless CHAT_LATENCY_LIVE=1.
 * PLAYWRIGHT_BASE_URL overrides the bind (use http://[::1]:5173 when Vite
 * is IPv6-only so reuseExistingServer does not start a second process).
 */
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:5173';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      testIgnore: /(?:chat-history-latency|document-ingestion-latency)/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chat-latency',
      testMatch: /chat-history-latency\.spec\.ts/,
      retries: 0,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chat-ingestion-latency',
      testMatch: /document-ingestion-latency\.spec\.ts/,
      retries: 0,
      workers: 1,
      use: {
        ...devices['Desktop Chrome'],
        trace: 'off',
        screenshot: 'off',
        video: 'off',
      },
    },
  ],
  webServer: {
    command: `pnpm exec vite --host ${baseURL.includes('[::1]') ? '::1' : '127.0.0.1'} --port 5173`,
    cwd: 'frontend',
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
