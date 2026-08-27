import { expect, test } from '@playwright/test';
import { installChatApiMocks, DEFAULT_PROJECT_ID } from './fixtures/chat-api';

test.describe('Project Documents & Document Viewer Suite', () => {
  test.beforeEach(async ({ page }) => {
    // Set active project so document fetch uses DEFAULT_PROJECT_ID
    await page.addInitScript((projectId) => {
      window.localStorage.setItem('v-assistant-active-project-id', projectId);
    }, DEFAULT_PROJECT_ID);

    await installChatApiMocks(page);

    // Mock project documents list – matches the pattern the app uses
    await page.route(`**/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents`, async (route) => {
      const method = route.request().method();
      if (method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            documents: [
              {
                document_id: 'doc-ready-1',
                project_id: DEFAULT_PROJECT_ID,
                filename: 'Quy_trinh_nghi_phep_2026.docx',
                media_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                byte_size: 45200,
                status: 'ready',
                error_code: null,
                page_count: 5,
                chunk_count: 12,
                ocr_page_count: 0,
                expires_at: '2027-08-20T10:00:00Z',
                created_at: '2026-08-20T10:00:00Z',
              },
              {
                document_id: 'doc-ready-2',
                project_id: DEFAULT_PROJECT_ID,
                filename: 'Huong_dan_thue_dien_tu.pdf',
                media_type: 'application/pdf',
                byte_size: 128000,
                status: 'ready',
                error_code: null,
                page_count: 20,
                chunk_count: 45,
                ocr_page_count: 0,
                expires_at: '2027-08-21T14:30:00Z',
                created_at: '2026-08-21T14:30:00Z',
              },
            ],
          }),
        });
        return;
      }
      await route.fallback();
    });
  });

  test('opens Project Documents panel via header button and shows document list', async ({ page }) => {
    await page.goto('/#dashboard');
    // Wait for dashboard to mount with the chat view active
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    // The "Project documents" header button only appears when projectDocumentsEnabled=true
    // and activeView='chat'. document-health mock returns feature=enabled so it should appear.
    const headerDocBtn = page.getByRole('button', { name: 'Project documents' });
    await expect(headerDocBtn).toBeVisible({ timeout: 10_000 });
    await headerDocBtn.click();

    // Panel slides in – verify documents are listed
    await expect(page.getByText('Quy_trinh_nghi_phep_2026.docx')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('Huong_dan_thue_dien_tu.pdf')).toBeVisible({ timeout: 10_000 });
  });

  test('uploads docx file via attachment button and attachment badge appears', async ({ page }) => {
    let uploaded = false;

    await page.route(
      `**/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents`,
      async (route) => {
        const method = route.request().method();
        if (method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              documents: uploaded
                ? [
                    {
                      document_id: 'doc-new-1',
                      project_id: DEFAULT_PROJECT_ID,
                      filename: 'Bang_luong_mau.docx',
                      media_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                      byte_size: 2048,
                      status: 'ready',
                      error_code: null,
                      page_count: 1,
                      chunk_count: 2,
                      ocr_page_count: 0,
                      expires_at: '2027-08-25T00:00:00Z',
                    },
                  ]
                : [],
            }),
          });
          return;
        }
        if (method === 'POST') {
          uploaded = true;
          await route.fulfill({
            status: 202,
            contentType: 'application/json',
            body: JSON.stringify({
              document_id: 'doc-new-1',
              status: 'received',
              upload_url: `/v1/cowork/chat/projects/${DEFAULT_PROJECT_ID}/documents/doc-new-1/source`,
            }),
          });
          return;
        }
        await route.fallback();
      },
      // Override the beforeEach mock for this test
    );

    await page.route(`**/documents/doc-new-1/source`, async (route) => {
      await route.fulfill({ status: 200, body: 'OK' });
    });
    await page.route(`**/documents/doc-new-1/complete`, async (route) => {
      await route.fulfill({
        status: 202,
        contentType: 'application/json',
        body: JSON.stringify({ document_id: 'doc-new-1', status: 'ready' }),
      });
    });

    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    // File input for chat attachments – always attached but hidden
    const fileInput = page.locator('input[type="file"]').first();
    await expect(fileInput).toBeAttached({ timeout: 15_000 });

    await fileInput.setInputFiles({
      name: 'Bang_luong_mau.docx',
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      buffer: Buffer.from('Mock docx binary data for test suite'),
    });

    // Attachment badge appears in composer area (data-testid="chat-attachment")
    await expect(page.getByTestId('chat-attachment').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('chat-attachment').first()).toContainText('Bang_luong_mau.docx');
  });

  test('views raw process documents extracted knowledge', async ({ page }) => {
    await page.route('**/api/v1/raw-documents**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          documents: [
            {
              filename: 'quy-trinh-cap-lai-cccd.pdf',
              extracted_filename: 'cap-lai-cccd.md',
              title: 'Thủ tục cấp lại căn cước công dân',
              source_type: 'pdf',
              size: 245000,
            },
          ],
        }),
      });
    });

    await page.goto('/#dashboard');
    await expect(page.locator('textarea')).toBeVisible({ timeout: 15_000 });

    // Switch to Raw Documents view via sidebar
    await page.getByRole('button', { name: 'Tài liệu quy trình' }).first().click();

    // Verify raw documents section renders
    await expect(page.getByText(/Tài liệu quy trình|căn cước công dân/i).first()).toBeVisible({
      timeout: 10_000,
    });
  });
});
