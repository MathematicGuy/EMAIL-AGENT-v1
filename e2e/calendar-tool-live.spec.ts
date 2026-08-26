import { expect, test } from '@playwright/test';

/**
 * Tier B: the real backend, the real chat UI, only Google's HTTP call faked.
 *
 * Started by `e2e/harness/tier_b_server.py` (see `e2e/harness/README.md`).
 * Every layer under test is production code -- intent routing, the per-user
 * binder, `fill_arguments`, the range guards, the Google request body -- so a
 * failure here is a product failure, not a mock drifting.
 *
 * These turns call a real model. Keep the case list short and the assertions
 * about *what was written*, never about the wording of the reply.
 */

const HARNESS = process.env.TIER_B_URL ?? 'http://127.0.0.1:8123';
const TURN_TIMEOUT = 180_000;

interface RecordedEvent {
  event_id: string;
  summary: string;
  start: { dateTime?: string; timeZone?: string; date?: string };
  grant_fingerprint: string;
}

test.describe('Calendar tool, live backend', () => {
  test.describe.configure({ mode: 'serial', timeout: 300_000 });

  test.beforeEach(async ({ page }) => {
    await page.goto('/#dashboard');
    // Mint the principal explicitly rather than racing the app's own guest
    // bootstrap, and do it through the page origin so the cookie lands in this
    // browser context. Every test then starts as a distinct, isolated user,
    // which is what makes the J1 fingerprint assertion mean anything.
    const session = await page.request.post('/v1/cowork/chat/guest-session');
    expect(session.ok(), `guest session failed: ${session.status()}`).toBeTruthy();
    await page.reload();
    await expect(page.locator('textarea')).toBeVisible({ timeout: 30_000 });
  });

  /** Stand in for the OAuth callback, for whichever principal this browser is. */
  async function seedGrant(page: import('@playwright/test').Page): Promise<string> {
    const response = await page.request.post(`${HARNESS}/__testing__/calendar-grant`, { data: {} });
    expect(response.ok(), `seeding the grant failed: ${response.status()}`).toBeTruthy();
    const body = await response.json();
    return body.grant_fingerprint as string;
  }

  async function readEvents(page: import('@playwright/test').Page) {
    const response = await page.request.get(`${HARNESS}/__testing__/events`);
    expect(response.ok()).toBeTruthy();
    return (await response.json()) as { count: number; conflicts: number; events: RecordedEvent[] };
  }

  async function clearEvents(page: import('@playwright/test').Page) {
    await page.request.delete(`${HARNESS}/__testing__/events`);
  }

  async function ask(page: import('@playwright/test').Page, prompt: string) {
    const replies = page.getByTestId('assistant-message-content');
    const before = await replies.count();
    await page.locator('textarea').first().fill(prompt);
    await page.getByTestId('chat-send').click();
    // The turn is over when the stop control goes away -- the same signal the
    // user reads. Waiting for an assistant bubble is not enough: the bubble
    // renders immediately to host the progress panel, so `toBeVisible` there
    // passes while the turn is still running and the tool has not been called.
    const stop = page.getByRole('button', { name: /Dừng tạo phản hồi|Stop generating/i });
    await stop.waitFor({ state: 'visible', timeout: 30_000 }).catch(() => undefined);
    await expect(stop).toBeHidden({ timeout: TURN_TIMEOUT });
    await expect(replies).toHaveCount(before + 1, { timeout: 30_000 });
  }

  test('the tool is composed and the browser has a principal', async ({ page }) => {
    const response = await page.request.get(`${HARNESS}/__testing__/whoami`);
    const body = await response.json();
    expect(body.composed_tools).toContain('create_calendar_event');
    expect(body.user_id, 'the frontend should have created a guest session').toBeTruthy();
  });

  test('a signed-in user without a grant is refused, not written for somebody else', async ({
    page,
  }) => {
    await page.request.delete(`${HARNESS}/__testing__/calendar-grant`);
    await clearEvents(page);

    await ask(page, 'Tạo lịch tập gym lúc 2 giờ sáng thứ Sáu.');
    await page.screenshot({ path: 'test-results/tier-b-01-no-grant.png', fullPage: true });

    // J2. The harness has a working environment token configured; a turn that
    // used it would show up here as a write with the environment fingerprint.
    expect((await readEvents(page)).count).toBe(0);
  });

  test('a prompt creates exactly one event through this user\'s own grant', async ({ page }) => {
    const fingerprint = await seedGrant(page);
    await clearEvents(page);

    await ask(page, 'Tạo lịch tập gym lúc 2 giờ sáng thứ Sáu.');
    await page.screenshot({ path: 'test-results/tier-b-02-created.png', fullPage: true });

    const recorded = await readEvents(page);
    expect(recorded.count).toBe(1);
    const [event] = recorded.events;
    // J1: written through the grant this browser seeded, not the environment.
    expect(event.grant_fingerprint).toBe(fingerprint);
    expect(event.start.timeZone).toBe('Asia/Ho_Chi_Minh');
    expect(event.start.dateTime).toContain('2026-08-28T02:00:00');
  });

  test('a question about the calendar answers instead of writing', async ({ page }) => {
    await seedGrant(page);
    await clearEvents(page);

    await ask(page, 'Google Calendar có tính năng nhắc lặp lại hàng tuần không?');
    await page.screenshot({ path: 'test-results/tier-b-03-no-false-write.png', fullPage: true });

    expect((await readEvents(page)).count).toBe(0);
  });

  // Expected to fail until the offset finding is fixed. Marked rather than
  // deleted so the suite turns red the day it starts passing, which is the
  // only signal that the fix landed. See docs/evaluations/CHAT/PROGRESS.md F6.
  test('the created event carries the calendar zone, not UTC', async ({ page }) => {
    await seedGrant(page);
    await clearEvents(page);

    await ask(page, 'Tạo lịch tập gym lúc 2 giờ sáng thứ Sáu.');

    const [event] = (await readEvents(page)).events;
    // `+00:00` here means Google files a 02:00 request at 09:00 local time,
    // while the reply tells the user 2AM.
    expect(event.start.dateTime).toBe('2026-08-28T02:00:00+07:00');
  });
});
