import { defineConfig, devices } from '@playwright/test';

/**
 * Frontend lives at Vite :5173. Chat API calls go through `/backend`.
 * The chat-latency project mocks those calls unless CHAT_LATENCY_LIVE=1.
 * PLAYWRIGHT_BASE_URL overrides the bind (use http://[::1]:5173 when Vite
 * is IPv6-only so reuseExistingServer does not start a second process).
 */
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:5173';
// Where the frontend's /backend and /v1 proxies point. The Tier B harness
// runs here instead of 8000 so it never displaces a running dev backend.
const backendOrigin =
  process.env.BACKEND_ORIGIN ?? (process.env.TIER_B ? 'http://127.0.0.1:8123' : 'http://127.0.0.1:8000');

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
      testIgnore: /(?:chat-history-latency|document-ingestion-latency|calendar-tool-live)/,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'chat-latency',
      testMatch: /chat-history-latency\.spec\.ts/,
      retries: 0,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // Tier B: real backend on TIER_B_URL, only Google's HTTP call faked.
      // Opt-in via TIER_B=1 -- these turns call a real model and cost money.
      // The opt-in is enforced by a group-level `test.skip` in the spec, not
      // here: the project stays defined so `--project=calendar-tier-b` remains
      // addressable and reports skips instead of failing as an unknown project.
      // Records video and every screenshot: the artifacts are the deliverable,
      // not a debugging aid, so they are captured on pass as well as failure.
      name: 'calendar-tier-b',
      testMatch: /calendar-tool-live\.spec\.ts/,
      retries: 0,
      workers: 1,
      use: {
        ...devices['Desktop Chrome'],
        video: 'on',
        screenshot: 'on',
        trace: 'on',
      },
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
  webServer: [
    {
      command: `pnpm exec vite --host ${baseURL.includes('[::1]') ? '::1' : '127.0.0.1'} --port 5173`,
      cwd: 'frontend',
      url: baseURL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: { BACKEND_ORIGIN: backendOrigin },
    },
    // Only started for the Tier B run. Its port is deliberately not 8000, so a
    // developer's own backend keeps that one.
    ...(process.env.TIER_B
      ? [
          {
            command: 'uv run python e2e/harness/tier_b_server.py',
            url: `${backendOrigin}/__testing__/events`,
            reuseExistingServer: true,
            timeout: 180_000,
          },
        ]
      : []),
  ],
});
