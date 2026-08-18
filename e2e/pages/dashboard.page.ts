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
    this.composer = page.getByPlaceholder('How can I help you today?');
    this.fileInput = page.getByLabel('Chọn tài liệu từ máy');
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
    const close = this.page.getByRole('button', { name: 'Close project documents' });
    if (await close.isVisible().catch(() => false)) await close.click();
  }

  async expandSidebar(): Promise<void> {
    const toggle = this.page.getByRole('button', { name: /Show sidebar/i });
    await expect(toggle.or(this.recents.first())).toBeVisible({ timeout: 20_000 });
    if (await toggle.isVisible().catch(() => false)) {
      await toggle.click();
    }
  }

  async openRecent(title: string): Promise<void> {
    await this.expandSidebar();
    await this.recentChat(title).click();
  }
}
