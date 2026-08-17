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
      mode: samples.every((item) => item.mode === 'mocked') ? 'mocked' as const : 'mixed' as const,
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

    expect(api.messagesFetchCount()).toBe(3);
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
    await page.goto('/#dashboard');
    const dashboard = new DashboardPage(page);
    await dashboard.expandSidebar();
    await expect(dashboard.recents.first()).toBeVisible({ timeout: 20_000 });
    const titles = await dashboard.recents.allTextContents();
    const unique = [...new Set(titles.map((title) => title.trim()).filter(Boolean))];
    test.skip(unique.length < 2, 'Need at least two saved chats to measure a live switch.');

    const firstChat = dashboard.recentChat(unique[0]);
    const secondChat = dashboard.recentChat(unique[1]);
    const fromId = await firstChat.getAttribute('data-chat-id');
    const toId = await secondChat.getAttribute('data-chat-id');
    if (!fromId || !toId) {
      test.skip(true, 'Recent chat buttons are missing data-chat-id.');
      return;
    }

    await firstChat.click();
    const firstMessages = page.getByTestId('chat-message');
    await expect(firstMessages.first()).toBeVisible({ timeout: 30_000 });

    const sample = await measureChatSwitch(page, {
      scenario: 'live-existing-chat-switch',
      mode: 'live',
      fromChatId: fromId,
      toChatId: toId,
      click: () => secondChat.click(),
      ready: page.getByTestId('chat-message').first(),
      stale: firstMessages.first(),
    });
    samples.push(sample);
    expect(sample.click_to_first_message_visible_ms).toBeGreaterThan(0);
  });
});
