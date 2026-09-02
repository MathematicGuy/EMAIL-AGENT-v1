import { expect, type Locator, type Page } from '@playwright/test';

export class DashboardPage {
  readonly page: Page;
  readonly recents: Locator;
  readonly loading: Locator;
  readonly stream: Locator;
  readonly composer: Locator;
  readonly fileInput: Locator;
  readonly sendButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.recents = page.getByTestId('recent-chat');
    this.loading = page.getByTestId('chat-history-loading');
    this.stream = page.getByTestId('chat-stream');
    this.composer = page.getByPlaceholder('Tôi có thể giúp gì cho bạn hôm nay?')
      .or(page.getByPlaceholder('How can I help you today?'))
      .or(page.locator('textarea'));
    this.fileInput = page.locator('input[type="file"]').first();
    this.sendButton = page.getByTestId('chat-send');
  }

  recentChat(title: string): Locator {
    return this.recents.filter({ hasText: title });
  }

  chatMessage(text: string, role: 'user' | 'assistant' = 'assistant'): Locator {
    return this.page.locator(`[data-testid="chat-message"][data-role="${role}"]`).filter({ hasText: text });
  }

  attachment(filename: string): Locator {
    return this.page.getByTestId('chat-attachment').filter({ hasText: filename });
  }

  async closeProjectDocuments(): Promise<void> {
    const close = this.page.getByRole('button', { name: /Close project documents|Đóng/i });
    if (await close.isVisible().catch(() => false)) await close.click();
  }

  async expandSidebar(): Promise<void> {
    // Wait for dashboard to be ready
    await this.page.locator('textarea').waitFor({ state: 'visible', timeout: 20_000 });

    // Expand sidebar if collapsed
    const toggle = this.page.getByRole('button', { name: 'Show sidebar' });
    if (await toggle.isVisible({ timeout: 1_000 }).catch(() => false)) {
      await toggle.click();
      await expect(this.page.getByText('DỰ ÁN', { exact: true })).toBeVisible({ timeout: 5_000 });
    }

    // Expand project accordion if not already expanded
    const expandBtn = this.page.getByRole('button', { name: /Expand Latency Project/i });
    if (await expandBtn.isVisible({ timeout: 1_000 }).catch(() => false)) {
      await expandBtn.click();
    }
  }

  async openRecent(title: string): Promise<void> {
    const item = this.recentChat(title);
    if (!await item.isVisible().catch(() => false)) {
      await this.expandSidebar();
    }
    await item.click();
  }
}
