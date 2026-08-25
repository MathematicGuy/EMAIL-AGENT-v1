import { expect, test } from '@playwright/test';
import {
  installChatApiMocks,
  DEFAULT_PROJECT_ID,
  CHAT_A_TITLE,
  CHAT_B_TITLE,
  CHAT_HEAVY_TITLE,
} from './fixtures/chat-api';

// Expand project accordion – projects load async so the button may appear after mount
async function expandProjectAccordion(page: import('@playwright/test').Page) {
  const expandBtn = page.getByRole('button', { name: /Expand Latency Project/i });
  try {
    await expandBtn.waitFor({ state: 'visible', timeout: 10_000 });
    await expandBtn.click();
  } catch {
    // Already expanded – fall through
  }
}

test.describe('AI Chat & Multi-Turn Reasoning Workflow Suite', () => {
  test.beforeEach(async ({ page }) => {
    await installChatApiMocks(page);
  });

  test('switches between multiple chat sessions and verifies transcript updates', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });
    await expandProjectAccordion(page);

    // 1. Select Chat A
    const chatAButton = page.getByTestId('recent-chat').filter({ hasText: CHAT_A_TITLE }).first();
    await expect(chatAButton).toBeVisible({ timeout: 15_000 });
    await chatAButton.click();

    // Verify transcript for Chat A – data-role="user" message with the marker text
    await expect(
      page.locator('[data-testid="chat-message"][data-role="user"]').filter({ hasText: 'LATENCY-MARKER-A' })
    ).toBeVisible({ timeout: 10_000 });

    // 2. Select Chat B
    const chatBButton = page.getByTestId('recent-chat').filter({ hasText: CHAT_B_TITLE }).first();
    await chatBButton.click();

    await expect(
      page.locator('[data-testid="chat-message"][data-role="user"]').filter({ hasText: 'LATENCY-MARKER-B' })
    ).toBeVisible({ timeout: 10_000 });
  });

  test('submits user message and renders streaming assistant reply', async ({ page }) => {
    // Mock SSE streaming reply for any session messages POST
    await page.route('**/sessions/*/messages', async (route) => {
      if (route.request().method() === 'POST') {
        const sseBody = [
          'event: message_start\ndata: {"message_id": "msg-1", "role": "assistant"}\n\n',
          'event: content_delta\ndata: {"delta": "Câu trả lời từ AI."}\n\n',
          'event: message_end\ndata: {"turn_id": "turn-1", "finish_reason": "stop"}\n\n',
        ].join('');
        await route.fulfill({
          status: 200,
          headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
          body: sseBody,
        });
        return;
      }
      await route.fallback();
    });

    // Also mock the session creation endpoint
    await page.route(`**/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/sessions`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ session_id: 'session-new-1', title: 'New Chat' }),
        });
        return;
      }
      await route.fallback();
    });

    await page.goto('/#dashboard');
    const composer = page.locator('textarea');
    await expect(composer).toBeVisible({ timeout: 15_000 });

    await composer.fill('Kiểm tra quy trình nghỉ phép');

    // Submit with Ctrl+Enter (matches composer keyboard handler)
    await composer.press('Control+Enter');

    // User message appears as a chat-message bubble
    await expect(
      page.locator('[data-testid="chat-message"][data-role="user"]').filter({ hasText: 'Kiểm tra quy trình' })
    ).toBeVisible({ timeout: 15_000 });

    // Assistant reply rendered
    await expect(
      page.locator('[data-testid="chat-message"][data-role="assistant"]').filter({ hasText: 'Câu trả lời' })
    ).toBeVisible({ timeout: 15_000 });
  });

  test('inspects citations and RAG evidence drawer', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });
    await expandProjectAccordion(page);

    const chatHeavyBtn = page.getByTestId('recent-chat').filter({ hasText: CHAT_HEAVY_TITLE }).first();
    await expect(chatHeavyBtn).toBeVisible({ timeout: 15_000 });
    await chatHeavyBtn.click();

    // Verify heavy session loaded – first user message has the marker
    await expect(
      page.locator('[data-testid="chat-message"][data-role="user"]').filter({ hasText: 'LATENCY-MARKER-HEAVY' })
    ).toBeVisible({ timeout: 10_000 });

    // Open citation badge if present
    const citationBadge = page
      .locator('button')
      .filter({ hasText: /Policy|Nguồn|Evidence/i })
      .first();
    if (await citationBadge.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await citationBadge.click();
      await expect(page.getByText(/Policy|Nguồn trích dẫn|Evidence/i).first()).toBeVisible({
        timeout: 5_000,
      });
    }
  });

  test('creates a new chat session via sidebar button', async ({ page }) => {
    await page.goto('/#dashboard');
    const composer = page.locator('textarea');
    await expect(composer).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'Tạo cuộc trò chuyện mới' }).first().click();

    await expect(composer).toBeVisible();
    await expect(composer).toHaveValue('');
  });
});
