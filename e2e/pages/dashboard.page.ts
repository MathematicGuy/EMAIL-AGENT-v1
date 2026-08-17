import { type Locator, type Page } from '@playwright/test';

export class DashboardPage {
  readonly page: Page;
  readonly recents: Locator;
  readonly loading: Locator;
  readonly stream: Locator;

  constructor(page: Page) {
    this.page = page;
    this.recents = page.getByTestId('recent-chat');
    this.loading = page.getByTestId('chat-history-loading');
    this.stream = page.getByTestId('chat-stream');
  }

  recentChat(title: string): Locator {
    return this.recents.filter({ hasText: title });
  }

  chatMessage(text: string, role: 'user' | 'assistant' = 'assistant'): Locator {
    return this.page.locator(`[data-testid="chat-message"][data-role="${role}"]`).filter({ hasText: text });
  }

  async openRecent(title: string): Promise<void> {
    await this.recentChat(title).click();
  }
}
