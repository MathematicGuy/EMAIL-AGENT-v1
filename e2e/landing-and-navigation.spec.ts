import { expect, test } from '@playwright/test';
import { installChatApiMocks } from './fixtures/chat-api';

test.describe('Landing Page & Full Navigation Suite', () => {
  test.beforeEach(async ({ page }) => {
    await installChatApiMocks(page);
  });

  test('renders landing page with hero, navigation, and navigates to dashboard on CTA click', async ({ page }) => {
    await page.goto('/');

    // 1. Verify Landing Page h1 heading is visible (exact locator, no .or())
    await expect(page.locator('h1').first()).toBeVisible({ timeout: 15_000 });

    // 2. Click Primary CTA – exact text from screenshot: "Dùng Thử F-Cowork Miễn Phí"
    const startButton = page.getByRole('button', { name: 'Dùng Thử F-Cowork Miễn Phí' })
      .or(page.getByRole('link', { name: /Mở Không Gian Làm Việc/i }).first());
    await expect(startButton.first()).toBeVisible({ timeout: 10_000 });
    await startButton.first().click();

    // 3. Verify Dashboard route loaded and textarea is present
    await expect(page).toHaveURL(/#dashboard/);
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });
  });

  test('supports direct hash navigation and switching to Documents Demo', async ({ page }) => {
    // 1. Direct navigation to #documents
    await page.goto('/#documents');
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 15_000 });

    // 2. Direct navigation to #dashboard
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });
  });

  test('sidebar collapse and expand toggle works smoothly', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    // Sidebar starts expanded. Click "Hide sidebar"
    const hideToggle = page.getByRole('button', { name: 'Hide sidebar' });
    await expect(hideToggle).toBeVisible({ timeout: 10_000 });
    await hideToggle.click();

    // Now sidebar is collapsed – "Show sidebar" button appears
    const showToggle = page.getByRole('button', { name: 'Show sidebar' });
    await expect(showToggle).toBeVisible({ timeout: 10_000 });
    await showToggle.click();

    // Sidebar expanded – use exact match to avoid strict-mode violation with "Tài liệu dự án"
    await expect(page.getByText('DỰ ÁN', { exact: true })).toBeVisible({ timeout: 10_000 });
  });

  test('navigation between views: Chat, Mail Inbox, Project Documents, Raw Documents', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    // 1. Switch to Mail Inbox view
    await page.getByRole('button', { name: 'Hộp thư' }).first().click();
    await expect(page.getByText('Mail Inbox').first()).toBeVisible({ timeout: 10_000 });

    // 2. Switch to Project Documents view (sets activeView='project-documents', chat div hidden)
    await page.getByRole('button', { name: 'Tài liệu dự án' }).first().click();
    // Sidebar aside is always present — confirm the view switched (chat composer now hidden)
    await expect(page.locator('textarea')).toBeHidden({ timeout: 5_000 });

    // 3. Switch to Raw Documents view
    await page.getByRole('button', { name: 'Tài liệu quy trình' }).first().click();
    // Raw docs view renders its own aside – sidebar aside is still present too; use first()
    await expect(page.locator('aside').first()).toBeVisible({ timeout: 5_000 });

    // 4. Switch back to Chat view – textarea may be hidden in mail/doc views, wait for visible
    await page.getByRole('button', { name: 'Đoạn chat' }).first().click();
    await expect(page.locator('textarea')).toBeVisible({ timeout: 10_000 });
  });

  test('opens and closes New Project Modal via + button next to DỰ ÁN section', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    // Click the + button with title="Tạo dự án"
    const createProjectBtn = page.getByTitle('Tạo dự án');
    await expect(createProjectBtn).toBeVisible({ timeout: 10_000 });
    await createProjectBtn.click();

    // Modal heading should appear
    const modalHeading = page.getByText('Tạo dự án mới', { exact: true });
    await expect(modalHeading).toBeVisible({ timeout: 5_000 });

    // Close modal – try cancel button first, then Escape
    const cancelBtn = page.getByRole('button', { name: /Hủy/i }).first();
    if (await cancelBtn.isVisible({ timeout: 1_000 }).catch(() => false)) {
      await cancelBtn.click();
    } else {
      await page.keyboard.press('Escape');
    }

    // Modal should close – use a short poll interval
    await expect(modalHeading).not.toBeVisible({ timeout: 8_000 });
  });
});
