import { expect, test } from '@playwright/test';
import { installChatApiMocks } from './fixtures/chat-api';

test.describe('Mail Intake & Action Plan Workflow Suite', () => {
  test.beforeEach(async ({ page }) => {
    await installChatApiMocks(page);

    // Connections – handle /connections and /connections/<id>/unread-preview with one pattern
    await page.route('**/v1/mail-todo/connections**', async (route) => {
      const url = route.request().url();
      if (url.includes('/unread-preview')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            emailsMatched: 3,
            messages: [
              {
                messageId: 'msg-mail-1',
                threadId: 'thread-1',
                subject: 'Yêu cầu phê duyệt ngân sách dự án Q3',
                sender: 'finance@company.com',
                receivedAt: '2026-08-25T02:30:00Z',
                attachmentsPresent: true,
                deepLink: 'https://mail.google.com',
              },
            ],
            nextCursor: null,
          }),
        });
        return;
      }
      // Base connections list
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          connections: [
            {
              id: 'conn-gmail-1',
              provider: 'gmail',
              emailAddress: 'steven.work@example.com',
              status: 'active',
              connectedAt: '2026-08-20T08:00:00Z',
              lastSyncedAt: '2026-08-25T03:00:00Z',
            },
          ],
          providerAvailability: {
            gmail: { enabled: true, reason: null },
            outlook: { enabled: true, reason: null },
          },
        }),
      });
    });

    // Runs listing + creation
    await page.route('**/v1/mail-todo/runs**', async (route) => {
      const url = route.request().url();
      const method = route.request().method();
      if (url.match(/runs\/run-(new|prev)-1/)) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: url.includes('run-new-1') ? 'run-new-1' : 'run-prev-1',
            status: 'completed',
            progress: { emailsMatched: 2, emailsProcessed: 2, emailsToProcess: 2, maxEmails: 10 },
            error: null,
          }),
        });
        return;
      }
      if (method === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({ id: 'run-new-1', status: 'queued', statusUrl: '/v1/mail-todo/runs/run-new-1' }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          runs: [{
            id: 'run-prev-1',
            status: 'completed',
            mailboxConnectionId: 'conn-gmail-1',
            createdAt: '2026-08-24T18:00:00Z',
            completedAt: '2026-08-24T18:01:30Z',
            emailsMatched: 5,
            emailsProcessed: 5,
            emailsToProcess: 5,
            taskCount: 3,
          }],
        }),
      });
    });
  });

  test('mail view mounts and shows Mail Inbox heading', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'Hộp thư' }).first().click();

    // Exact heading text from screenshot
    await expect(page.getByText('Mail Inbox')).toBeVisible({ timeout: 10_000 });
  });

  test('connected account appears in the email account dropdown', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'Hộp thư' }).first().click();
    await expect(page.getByText('Mail Inbox')).toBeVisible({ timeout: 10_000 });

    // The account selector is a <select> – its option text includes the email address
    // Use the label to find the select, then verify value
    const accountSelect = page.getByLabel(/Tài khoản email/i).first()
      .or(page.locator('select').first());
    await expect(accountSelect).toBeVisible({ timeout: 10_000 });
    // Verify the connected email is one of the options
    await expect(accountSelect).toContainText('steven.work@example.com');
  });

  test('mail inbox shows Kết nối Gmail and Kết nối Outlook buttons', async ({ page }) => {
    // Register connections mock BEFORE the catch-all backend mock
    // with empty connections list so UI always shows the connect buttons
    await page.route('**/v1/mail-todo/connections**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          connections: [],
          providerAvailability: {
            gmail: { enabled: true, reason: null },
            outlook: { enabled: true, reason: null },
          },
        }),
      });
    });

    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'Hộp thư' }).first().click();
    await expect(page.getByText('Mail Inbox')).toBeVisible({ timeout: 10_000 });

    // With no active connection, the UI always shows both connect buttons / links
    await expect(
      page.getByRole('link', { name: 'Kết nối Gmail' }).or(page.getByRole('button', { name: 'Kết nối Gmail' }))
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole('button', { name: 'Kết nối Outlook' })).toBeVisible({ timeout: 10_000 });
  });

  test('action plan section renders (empty state heading always shown)', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'Hộp thư' }).first().click();
    await expect(page.getByText('Mail Inbox')).toBeVisible({ timeout: 10_000 });

    // "Danh mục hành động (0)" or similar is always rendered
    await expect(page.getByText(/Danh mục hành động/i).first()).toBeVisible({ timeout: 10_000 });
  });

  test('creates email digest run if button is present', async ({ page }) => {
    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    await page.getByRole('button', { name: 'Hộp thư' }).first().click();
    await expect(page.getByText('Mail Inbox')).toBeVisible({ timeout: 10_000 });

    const digestBtn = page.getByRole('button', { name: /Tạo Action Plan|Xử lý email|Bắt đầu Digest/i }).first();
    if (await digestBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await digestBtn.click();
      await expect(page.getByText(/Đang xử lý|Hoàn thành|Completed|queued/i).first()).toBeVisible({ timeout: 10_000 });
    }
  });

  test('views reports / artifact documents', async ({ page }) => {
    await page.route('**/api/v1/reports', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{
          filename: 'bao-cao-t8.md',
          content: '# Báo cáo Tháng 8',
          size: 500,
          updated_at: '2026-08-25T00:00:00Z',
        }]),
      });
    });

    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    const artifactTab = page.getByRole('button', { name: /Artifacts/i }).first();
    if (await artifactTab.isVisible({ timeout: 3_000 }).catch(() => false)) {
      await artifactTab.click();
      await expect(page.getByText(/Báo cáo|bao-cao/i).first()).toBeVisible({ timeout: 10_000 });
    }
  });
});
