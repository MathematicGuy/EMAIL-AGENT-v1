import { expect, test } from '@playwright/test';
import {
  CHAT_A_ID,
  CHAT_A_MARKER,
  CHAT_A_TITLE,
  CHAT_B_ID,
  CHAT_B_MARKER,
  CHAT_B_TITLE,
  CHAT_HEAVY_ID,
  CHAT_HEAVY_MARKER,
  CHAT_HEAVY_TITLE,
  installChatApiMocks,
} from './fixtures/chat-api';
import {
  measureChatSwitch,
  openDashboard,
  seedLiveRecents,
  summarize,
  updateTrackLatestRun,
  writeLatencyReport,
  type ChatSwitchSample,
} from './helpers/chat-latency';
import { DashboardPage } from './pages/dashboard.page';

const samples: ChatSwitchSample[] = [];
const generatedAt = new Date().toISOString();

test.describe('chat history loading latency', () => {
  test.describe.configure({ mode: 'serial' });

  test.afterAll(() => {
    if (samples.length === 0) return;
    const report = {
      generated_at: generatedAt,
      git_sha: process.env.GITHUB_SHA ?? process.env.GIT_SHA ?? null,
      mode: samples.every((item) => item.mode === 'mocked')
        ? 'mocked' as const
        : samples.every((item) => item.mode === 'live')
          ? 'live' as const
          : 'mixed' as const,
      browser: 'chromium',
      samples,
      summary: summarize(samples),
    };
    const reportPath = writeLatencyReport(report);
    updateTrackLatestRun(report, reportPath);
  });

  test('records click-to-visible on a cold switch with an instant mocked API', async ({ page }) => {
    const api = await installChatApiMocks(page);
    const dashboard = new DashboardPage(page);
    await openDashboard(page);

    const sample = await measureChatSwitch(page, {
      scenario: 'mocked-instant-cold-switch',
      mode: 'mocked',
      fromChatId: null,
      toChatId: CHAT_A_ID,
      click: () => dashboard.openRecent(CHAT_A_TITLE),
      ready: dashboard.chatMessage(`Answer ${CHAT_A_MARKER}`),
      messagesFetchCount: api.messagesFetchCount,
    });
    samples.push(sample);

    await expect(dashboard.chatMessage(`Answer ${CHAT_A_MARKER}`)).toBeVisible();
    expect(sample.click_to_first_message_visible_ms).toBeLessThan(1_500);
    expect(sample.response_to_first_message_visible_ms ?? 1_500).toBeLessThan(800);
    expect(sample.messages_fetch_count_after).toBeGreaterThanOrEqual(1);
  });

  test('records a 2500ms API delay that matches the reported user wait', async ({ page }) => {
    test.setTimeout(45_000);
    const api = await installChatApiMocks(page, {
      delayMsBySession: { [CHAT_B_ID]: 2_500 },
    });
    const dashboard = new DashboardPage(page);
    await openDashboard(page);

    const sample = await measureChatSwitch(page, {
      scenario: 'mocked-2500ms-user-report',
      mode: 'mocked',
      fromChatId: null,
      toChatId: CHAT_B_ID,
      click: () => dashboard.openRecent(CHAT_B_TITLE),
      ready: dashboard.chatMessage(`Answer ${CHAT_B_MARKER}`),
      messagesFetchCount: api.messagesFetchCount,
    });
    samples.push(sample);

    await expect(dashboard.chatMessage(`Answer ${CHAT_B_MARKER}`)).toBeVisible();
    expect(sample.loading_indicator_observed).toBe(true);
    expect(sample.request_duration_ms ?? 0).toBeGreaterThanOrEqual(2_400);
    expect(sample.click_to_first_message_visible_ms).toBeGreaterThanOrEqual(2_400);
    expect(sample.click_to_first_message_visible_ms).toBeLessThan(4_500);
  });

  test('records stale-content flash and refetch when switching A → B → A', async ({ page }) => {
    const api = await installChatApiMocks(page, {
      delayMsBySession: { [CHAT_A_ID]: 400, [CHAT_B_ID]: 400 },
    });
    const dashboard = new DashboardPage(page);
    await openDashboard(page);

    const firstA = await measureChatSwitch(page, {
      scenario: 'mocked-repeat-first-a',
      mode: 'mocked',
      fromChatId: null,
      toChatId: CHAT_A_ID,
      click: () => dashboard.openRecent(CHAT_A_TITLE),
      ready: dashboard.chatMessage(`Answer ${CHAT_A_MARKER}`),
      messagesFetchCount: api.messagesFetchCount,
    });
    const toB = await measureChatSwitch(page, {
      scenario: 'mocked-repeat-a-to-b',
      mode: 'mocked',
      fromChatId: CHAT_A_ID,
      toChatId: CHAT_B_ID,
      click: () => dashboard.openRecent(CHAT_B_TITLE),
      ready: dashboard.chatMessage(`Answer ${CHAT_B_MARKER}`),
      stale: dashboard.chatMessage(`Answer ${CHAT_A_MARKER}`),
      messagesFetchCount: api.messagesFetchCount,
    });
    const backToA = await measureChatSwitch(page, {
      scenario: 'mocked-repeat-b-to-a',
      mode: 'mocked',
      fromChatId: CHAT_B_ID,
      toChatId: CHAT_A_ID,
      click: () => dashboard.openRecent(CHAT_A_TITLE),
      ready: dashboard.chatMessage(`Answer ${CHAT_A_MARKER}`),
      stale: dashboard.chatMessage(`Answer ${CHAT_B_MARKER}`),
      messagesFetchCount: api.messagesFetchCount,
    });
    samples.push(firstA, toB, backToA);

    expect(api.messagesFetchCount()).toBeGreaterThanOrEqual(3);
    expect(backToA.click_to_first_message_visible_ms).toBeLessThan(150);
    expect(toB.stale_content_visible_ms ?? 0).toBe(0);
    expect(backToA.stale_content_visible_ms ?? 0).toBe(0);
  });

  test('records frontend cost of a heavy history payload', async ({ page }) => {
    const api = await installChatApiMocks(page, {
      extraTurnsForHeavy: 16,
      evidencePerHeavyTurn: 5,
      evidenceContentChars: 4_000,
    });
    const dashboard = new DashboardPage(page);
    await openDashboard(page);

    const sample = await measureChatSwitch(page, {
      scenario: 'mocked-heavy-payload',
      mode: 'mocked',
      fromChatId: null,
      toChatId: CHAT_HEAVY_ID,
      click: () => dashboard.openRecent(CHAT_HEAVY_TITLE),
      ready: dashboard.chatMessage(`Answer ${CHAT_HEAVY_MARKER}`),
      messagesFetchCount: api.messagesFetchCount,
    });
    samples.push(sample);

    await expect(dashboard.chatMessage(`Answer ${CHAT_HEAVY_MARKER}`)).toBeVisible();
    expect(sample.turn_count).toBe(16);
    expect(sample.payload_bytes ?? 0).toBeLessThan(80_000);
    expect(sample.click_to_first_message_visible_ms).toBeLessThan(3_000);
  });

  test('live stack switch latency @live', async ({ page }) => {
    test.skip(!process.env.CHAT_LATENCY_LIVE, 'Set CHAT_LATENCY_LIVE=1 with frontend + API running.');
    test.setTimeout(90_000);

    const [firstId, secondId, thirdId] = await seedLiveRecents(page);
    const dashboard = new DashboardPage(page);
    await dashboard.expandSidebar();
    const firstChat = page.locator(`[data-testid="recent-chat"][data-chat-id="${firstId}"]`);
    const secondChat = page.locator(`[data-testid="recent-chat"][data-chat-id="${secondId}"]`);
    const thirdChat = page.locator(`[data-testid="recent-chat"][data-chat-id="${thirdId}"]`);
    await expect(firstChat).toBeVisible({ timeout: 20_000 });
    await expect(secondChat).toBeVisible();
    await expect(thirdChat).toBeVisible();

    await firstChat.click();
    await expect(dashboard.chatMessage('Live latency seed 1')).toBeVisible({ timeout: 30_000 });

    const cold = await measureChatSwitch(page, {
      scenario: 'live-cold-switch',
      mode: 'live',
      fromChatId: firstId,
      toChatId: secondId,
      click: () => secondChat.click(),
      ready: dashboard.chatMessage('Live latency seed 2'),
      stale: dashboard.chatMessage('Live latency seed 1'),
    });
    samples.push(cold);

    const repeat = await measureChatSwitch(page, {
      scenario: 'live-repeat-switch',
      mode: 'live',
      fromChatId: secondId,
      toChatId: firstId,
      click: () => firstChat.click(),
      ready: dashboard.chatMessage('Live latency seed 1'),
      stale: dashboard.chatMessage('Live latency seed 2'),
    });
    samples.push(repeat);

    const prefetchGet = page.waitForResponse((response) => {
      return response.request().method() === 'GET'
        && response.url().includes(`/sessions/${encodeURIComponent(thirdId)}/messages`);
    }, { timeout: 15_000 });
    await thirdChat.hover();
    await prefetchGet;

    const prefetch = await measureChatSwitch(page, {
      scenario: 'live-prefetch-switch',
      mode: 'live',
      fromChatId: firstId,
      toChatId: thirdId,
      click: () => thirdChat.click(),
      ready: dashboard.chatMessage('Live latency seed 3'),
      stale: dashboard.chatMessage('Live latency seed 1'),
    });
    samples.push(prefetch);
    expect(cold.click_to_first_message_visible_ms).toBeGreaterThan(0);
    expect(repeat.click_to_first_message_visible_ms).toBeGreaterThan(0);
    expect(prefetch.click_to_first_message_visible_ms).toBeGreaterThan(0);
    expect(cold.request_duration_ms ?? 0).toBeGreaterThan(0);
  });
});
