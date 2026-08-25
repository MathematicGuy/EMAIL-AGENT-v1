import { expect, test } from '@playwright/test';
import { installChatApiMocks, DEFAULT_PROJECT_ID } from './fixtures/chat-api';
import path from 'node:path';
import fs from 'node:fs';

const SCREENSHOTS_DIR = path.resolve('evaluations', 'screenshots');

test.describe('Document upload worker states and error handling', () => {
  test.beforeAll(() => {
    fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
  });

  test('reproduces and verifies document worker unavailable error in UI', async ({ page }) => {
    // 1. Install base mocks
    await installChatApiMocks(page);

    // Mock project document listing
    await page.route(`**/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents`, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ documents: [] }),
        });
        return;
      }
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Project document worker unavailable' }),
        });
        return;
      }
      await route.fallback();
    });

    // 2. Navigate to chat dashboard
    await page.goto('/#dashboard');
    const composer = page.locator('textarea');
    await expect(composer).toBeVisible({ timeout: 15_000 });

    // 3. Upload the test document
    const fileInput = page.getByLabel('Chọn tài liệu từ máy').first();
    await fileInput.setInputFiles({
      name: '49_2019_QH14_402073.docx',
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      buffer: Buffer.from('Mock docx content for testing'),
    });

    // 4. Verify the error UI appears matching the user screenshot
    const errorAlert = page.getByText('Project document worker unavailable');
    await expect(errorAlert).toBeVisible({ timeout: 10_000 });

    const attachmentBadge = page.getByTestId('chat-attachment');
    await expect(attachmentBadge).toBeVisible();
    await expect(attachmentBadge).toContainText('49_2019_QH14_402073.docx');
    await expect(attachmentBadge).toContainText('Lỗi');

    // 5. Capture screenshot of reproduced error
    const screenshotPath = path.join(SCREENSHOTS_DIR, 'document-worker-unavailable-error.png');
    await page.screenshot({
      path: screenshotPath,
      fullPage: true,
    });
    expect(fs.existsSync(screenshotPath)).toBe(true);
  });

  test('verifies successful document upload flow when worker is available', async ({ page }) => {
    await installChatApiMocks(page);

    let docStatus = 'received';

    // Mock document upload and complete endpoints as successful
    await page.route(`**/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents`, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            documents: docStatus === 'ready' ? [{
              document_id: 'doc-test-1',
              project_id: DEFAULT_PROJECT_ID,
              filename: '49_2019_QH14_402073.docx',
              media_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              byte_size: 30,
              status: 'ready',
              created_at: new Date().toISOString(),
            }] : [],
          }),
        });
        return;
      }
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            document_id: 'doc-test-1',
            status: 'received',
            upload_url: `/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents/doc-test-1/source`,
          }),
        });
        return;
      }
      await route.fallback();
    });

    await page.route(`**/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents/doc-test-1/source`, async (route) => {
      await route.fulfill({ status: 200, body: 'OK' });
    });

    await page.route(`**/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents/doc-test-1/complete`, async (route) => {
      docStatus = 'ready';
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ document_id: 'doc-test-1', status: 'received' }),
      });
    });

    await page.route(`**/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents/doc-test-1`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          document_id: 'doc-test-1',
          project_id: DEFAULT_PROJECT_ID,
          filename: '49_2019_QH14_402073.docx',
          media_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          byte_size: 30,
          status: 'ready',
          created_at: new Date().toISOString(),
        }),
      });
    });

    await page.goto('/#dashboard');
    const composer = page.locator('textarea');
    await expect(composer).toBeVisible({ timeout: 15_000 });

    const fileInput = page.getByLabel('Chọn tài liệu từ máy').first();
    await fileInput.setInputFiles({
      name: '49_2019_QH14_402073.docx',
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      buffer: Buffer.from('Mock docx content for testing'),
    });

    const attachmentBadge = page.getByTestId('chat-attachment');
    await expect(attachmentBadge).toBeVisible();
    await expect(attachmentBadge).toContainText('49_2019_QH14_402073.docx');

    // Verify error alert is NOT present
    const errorAlert = page.getByText('Project document worker unavailable');
    await expect(errorAlert).not.toBeVisible();

    // Capture screenshot of successful upload state
    const screenshotPath = path.join(SCREENSHOTS_DIR, 'document-upload-successful.png');
    await page.screenshot({
      path: screenshotPath,
      fullPage: true,
    });
    expect(fs.existsSync(screenshotPath)).toBe(true);
  });
});
