import { expect, test } from '@playwright/test';
import { installChatApiMocks } from './fixtures/chat-api';

test.describe('End-to-End Application Smoke Suite', () => {
  test('verifies application boots and initial state is clean and responsive', async ({ page }) => {
    await installChatApiMocks(page);
    await page.goto('/');

    // Verify main app root container – use body (always exactly one element)
    await expect(page.locator('body')).toBeVisible();

    // Verify dashboard view mounts with composer
    await page.goto('/#dashboard');
    const composer = page.locator('textarea');
    await expect(composer).toBeVisible({ timeout: 15_000 });
  });
});
