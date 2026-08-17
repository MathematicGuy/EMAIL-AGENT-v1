import { defineConfig, devices } from '@playwright/test';

/**
 * Frontend lives at Vite :5173. Chat API calls go through `/backend`.
 * The chat-latency project mocks those calls unless CHAT_LATENCY_LIVE=1.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      testIgnore: /chat-history-latency/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chat-latency',
      testMatch: /chat-history-latency\.spec\.ts/,
      retries: 0,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'pnpm exec vite --host 127.0.0.1 --port 5173',
    cwd: 'frontend',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
