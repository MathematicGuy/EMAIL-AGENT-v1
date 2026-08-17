import { expect, type Locator, type Page, type Response } from '@playwright/test';
import { mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import path from 'node:path';

export const LATENCY_TRACK_DIR = path.join('evaluations', 'CHAT', 'latency');
export const LATENCY_RUNS_DIR = path.join(LATENCY_TRACK_DIR, 'runs');
export const LATENCY_TRACK_FILE = path.join(LATENCY_TRACK_DIR, 'TRACK.md');

export interface ChatSwitchSample {
  scenario: string;
  mode: 'mocked' | 'live';
  from_chat_id: string | null;
  to_chat_id: string;
  click_to_first_message_visible_ms: number;
  click_to_response_ms: number | null;
  response_to_first_message_visible_ms: number | null;
  request_duration_ms: number | null;
  payload_bytes: number | null;
  turn_count: number | null;
  loading_indicator_observed: boolean;
  stale_content_visible_ms: number | null;
  messages_fetch_count_after: number | null;
  notes?: string;
}

export interface LatencyRunReport {
  generated_at: string;
  git_sha: string | null;
  mode: 'mocked' | 'live' | 'mixed';
  browser: string;
  samples: ChatSwitchSample[];
  summary: {
    scenario: string;
    n: number;
    p50_click_to_visible_ms: number;
    p95_click_to_visible_ms: number;
    max_click_to_visible_ms: number;
    p50_request_duration_ms: number | null;
    p50_frontend_after_response_ms: number | null;
  }[];
}

const TRACK_START = '<!-- LATENCY-TRACK:SYNTHETIC-START -->';
const TRACK_END = '<!-- LATENCY-TRACK:SYNTHETIC-END -->';

export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index];
}

export function summarize(samples: ChatSwitchSample[]): LatencyRunReport['summary'] {
  const byScenario = new Map<string, ChatSwitchSample[]>();
  for (const sample of samples) {
    const bucket = byScenario.get(sample.scenario) ?? [];
    bucket.push(sample);
    byScenario.set(sample.scenario, bucket);
  }
  return [...byScenario.entries()].map(([scenario, group]) => {
    const visible = group.map((item) => item.click_to_first_message_visible_ms);
    const request = group
      .map((item) => item.request_duration_ms)
      .filter((item): item is number => item !== null);
    const frontend = group
      .map((item) => item.response_to_first_message_visible_ms)
      .filter((item): item is number => item !== null);
    return {
      scenario,
      n: group.length,
      p50_click_to_visible_ms: percentile(visible, 50),
      p95_click_to_visible_ms: percentile(visible, 95),
      max_click_to_visible_ms: Math.max(...visible),
      p50_request_duration_ms: request.length ? percentile(request, 50) : null,
      p50_frontend_after_response_ms: frontend.length ? percentile(frontend, 50) : null,
    };
  });
}

export function writeLatencyReport(report: LatencyRunReport, filename?: string): string {
  mkdirSync(LATENCY_RUNS_DIR, { recursive: true });
  const stamp = report.generated_at.replace(/[:.]/g, '-');
  const dest = path.join(LATENCY_RUNS_DIR, filename ?? `${stamp}.json`);
  writeFileSync(dest, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  return dest;
}

export function updateTrackLatestRun(report: LatencyRunReport, reportPath: string): void {
  if (!existsSync(LATENCY_TRACK_FILE)) return;
  const current = readFileSync(LATENCY_TRACK_FILE, 'utf8');
  const rows = report.summary
    .map((row) => {
      const request = row.p50_request_duration_ms === null ? '—' : String(row.p50_request_duration_ms);
      const frontend = row.p50_frontend_after_response_ms === null
        ? '—'
        : String(row.p50_frontend_after_response_ms);
      return `| ${row.scenario} | ${row.n} | ${row.p50_click_to_visible_ms} | ${row.p95_click_to_visible_ms} | ${row.max_click_to_visible_ms} | ${request} | ${frontend} |`;
    })
    .join('\n');
  const block = [
    TRACK_START,
    '',
    `Last synthetic run: \`${report.generated_at}\` · browser \`${report.browser}\` · report \`${reportPath.replace(/\\/g, '/')}\``,
    '',
    '| Scenario | n | p50 click→visible (ms) | p95 | max | p50 API (ms) | p50 UI after API (ms) |',
    '|---|---:|---:|---:|---:|---:|---:|',
    rows,
    '',
    TRACK_END,
  ].join('\n');
  const next = current.includes(TRACK_START) && current.includes(TRACK_END)
    ? current.replace(new RegExp(`${TRACK_START}[\\s\\S]*?${TRACK_END}`), block)
    : `${current.trimEnd()}\n\n${block}\n`;
  writeFileSync(LATENCY_TRACK_FILE, next, 'utf8');
}

export async function measureChatSwitch(
  page: Page,
  input: {
    scenario: string;
    mode: 'mocked' | 'live';
    fromChatId: string | null;
    toChatId: string;
    click: () => Promise<void>;
    ready: Locator;
    stale?: Locator;
    messagesFetchCount?: () => number;
  },
): Promise<ChatSwitchSample> {
  const loading = page.getByTestId('chat-history-loading');
  let loadingObserved = false;
  const watchLoading = loading.waitFor({ state: 'visible', timeout: 2_000 })
    .then(() => {
      loadingObserved = true;
    })
    .catch(() => undefined);

  const responsePromise = page.waitForResponse((response) => {
    return response.request().method() === 'GET'
      && response.url().includes(`/sessions/${encodeURIComponent(input.toChatId)}/messages`);
  }, { timeout: 30_000 });

  const clickAt = Date.now();
  await input.click();

  let staleMs: number | null = null;
  const watchStale = (async () => {
    if (!input.stale) return;
    const seen = await input.stale.isVisible().catch(() => false);
    if (!seen) {
      staleMs = 0;
      return;
    }
    const staleStart = Date.now();
    await input.stale.waitFor({ state: 'hidden', timeout: 30_000 });
    staleMs = Date.now() - staleStart;
  })();

  let response: Response | null = null;
  let responseAt: number | null = null;
  try {
    response = await responsePromise;
    responseAt = Date.now();
  } catch {
    response = null;
  }

  await expect(input.ready).toBeVisible({ timeout: 30_000 });
  const visibleAt = Date.now();
  await Promise.all([watchLoading, watchStale]);

  let requestDuration: number | null = null;
  let payloadBytes: number | null = null;
  let turnCount: number | null = null;
  if (response) {
    const timing = response.request().timing();
    if (Number.isFinite(timing.responseEnd) && timing.responseEnd >= 0) {
      requestDuration = Math.round(timing.responseEnd);
    } else if (responseAt !== null) {
      requestDuration = responseAt - clickAt;
    }
    const body = await response.body().catch(() => null);
    if (body) {
      payloadBytes = body.byteLength;
      try {
        const parsed = JSON.parse(body.toString('utf8')) as { turns?: unknown[] };
        turnCount = Array.isArray(parsed.turns) ? parsed.turns.length : null;
      } catch {
        turnCount = null;
      }
    }
  }

  return {
    scenario: input.scenario,
    mode: input.mode,
    from_chat_id: input.fromChatId,
    to_chat_id: input.toChatId,
    click_to_first_message_visible_ms: visibleAt - clickAt,
    click_to_response_ms: responseAt === null ? null : responseAt - clickAt,
    response_to_first_message_visible_ms:
      responseAt === null ? null : Math.max(0, visibleAt - responseAt),
    request_duration_ms: requestDuration,
    payload_bytes: payloadBytes,
    turn_count: turnCount,
    loading_indicator_observed: loadingObserved,
    stale_content_visible_ms: staleMs,
    messages_fetch_count_after: input.messagesFetchCount?.() ?? null,
  };
}

export async function openDashboard(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem('v-assistant-active-project-id', 'project-latency');
  });
  await page.goto('/#dashboard');
  await expect(page.getByTestId('recent-chat').first()).toBeVisible({ timeout: 20_000 });
}
