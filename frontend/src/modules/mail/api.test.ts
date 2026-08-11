import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MailApiError,
  createDigestRun,
  getUnreadPreview,
  listConnections,
  listDigestRuns,
} from './api';

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Mail API client', () => {
  it('uses the backend mail-todo connection and preview contracts', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ connections: [{ id: 'mbx-1' }] }))
      .mockResolvedValueOnce(response({ emailsMatched: 0, messages: [], nextCursor: null }));
    vi.stubGlobal('fetch', fetchMock);

    await listConnections();
    await getUnreadPreview('mbx/1', 20);

    expect(String(fetchMock.mock.calls[0][0])).toContain('/v1/mail-todo/connections');
    expect(String(fetchMock.mock.calls[1][0])).toContain(
      '/v1/mail-todo/connections/mbx%2F1/unread-preview?limit=20'
    );
  });

  it('sends the selected mailbox, limit, and stable idempotency key', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response({ id: 'run-1', status: 'queued', statusUrl: '/v1/mail-todo/runs/run-1' }, 202)
    );
    vi.stubGlobal('fetch', fetchMock);

    await createDigestRun({
      mailboxConnectionId: 'mbx-1',
      maxEmails: 20,
      idempotencyKey: 'mail-idem-1',
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.headers).toMatchObject({ 'Idempotency-Key': 'mail-idem-1' });
    expect(JSON.parse(String(init.body))).toEqual({
      mailboxConnectionId: 'mbx-1',
      maxEmails: 20,
    });
  });

  it('maps history query parameters and FastAPI detail errors', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response({ runs: [] }))
      .mockResolvedValueOnce(response({ detail: 'Gmail reauthorization required' }, 409));
    vi.stubGlobal('fetch', fetchMock);

    await listDigestRuns('mbx-1', 20);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/v1/mail-todo/runs?mailboxConnectionId=mbx-1&limit=20'
    );
    await expect(getUnreadPreview('mbx-1')).rejects.toEqual(
      expect.objectContaining<Partial<MailApiError>>({
        status: 409,
        message: 'Gmail reauthorization required',
      })
    );
  });
});
