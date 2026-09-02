import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  DigestRunView,
  DigestTask,
  MailboxConnection,
} from '../../modules/mail/api';

const mailApi = vi.hoisted(() => ({
  createDigestRun: vi.fn(),
  getDigestRun: vi.fn(),
  getDigestTasks: vi.fn(),
  getSelectedMailboxId: vi.fn(),
  listConnections: vi.fn(),
  newIdempotencyKey: vi.fn(() => 'mail-test-key'),
  setSelectedMailboxId: vi.fn(),
}));

vi.mock('../../modules/mail/api', () => mailApi);

import { runMailScanProtocol, type MailScanSnapshot } from './mailScanProtocol';

function connection(
  id: string,
  provider: MailboxConnection['provider'],
  status = 'active',
): MailboxConnection {
  return {
    id,
    provider,
    status,
    emailAddress: `${id}@example.com`,
    scopes: [],
    createdAt: '2026-08-26T00:00:00Z',
  };
}

function run(
  id: string,
  status: DigestRunView['status'],
  progress: Partial<DigestRunView['progress']> = {},
): DigestRunView {
  return {
    id,
    status,
    progress: {
      emailsMatched: 0,
      emailsProcessed: 0,
      emailsToProcess: 0,
      maxEmails: 10,
      ...progress,
    },
    error: null,
  };
}

function task(id: string): DigestTask {
  return { task_id: id } as DigestTask;
}

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('runMailScanProtocol', () => {
  it('prefers remembered active mailboxes, falls back to the first active mailbox, and uses provider query rules', async () => {
    mailApi.listConnections.mockResolvedValue({
      connections: [
        connection('gmail-first', 'gmail'),
        connection('gmail-remembered', 'gmail'),
        connection('outlook-first', 'outlook'),
        connection('outlook-inactive', 'outlook', 'expired'),
      ],
      providerAvailability: {},
    });
    mailApi.getSelectedMailboxId.mockImplementation((provider: string) =>
      provider === 'gmail' ? 'gmail-remembered' : 'outlook-inactive'
    );
    mailApi.createDigestRun.mockImplementation(async (input: { mailboxConnectionId: string }) => ({
      id: `${input.mailboxConnectionId}-run`,
      status: 'queued',
      statusUrl: '/status',
    }));
    mailApi.getDigestRun.mockImplementation(async (id: string) => run(id, 'succeeded'));
    mailApi.getDigestTasks.mockResolvedValue([]);

    await runMailScanProtocol({
      providers: ['gmail', 'outlook'],
      signal: new AbortController().signal,
    });

    expect(mailApi.createDigestRun).toHaveBeenCalledWith(expect.objectContaining({
      mailboxConnectionId: 'gmail-remembered',
      maxEmails: 10,
      query: 'is:unread in:inbox category:primary',
      idempotencyKey: 'mail-test-key',
    }));
    expect(mailApi.createDigestRun).toHaveBeenCalledWith(expect.objectContaining({
      mailboxConnectionId: 'outlook-first',
      maxEmails: 10,
      query: undefined,
    }));
    expect(mailApi.setSelectedMailboxId).toHaveBeenCalledWith('gmail', 'gmail-remembered');
    expect(mailApi.setSelectedMailboxId).toHaveBeenCalledWith('outlook', 'outlook-first');
  });

  it('publishes connecting, queued, running, and terminal snapshots', async () => {
    vi.useFakeTimers();
    mailApi.listConnections.mockResolvedValue({
      connections: [connection('gmail-1', 'gmail')],
      providerAvailability: {},
    });
    mailApi.createDigestRun.mockResolvedValue({ id: 'run-1', status: 'queued', statusUrl: '/status' });
    mailApi.getDigestRun
      .mockResolvedValueOnce(run('run-1', 'queued', { emailsMatched: 4, emailsToProcess: 4 }))
      .mockResolvedValueOnce(run('run-1', 'running', {
        emailsMatched: 4, emailsProcessed: 2, emailsToProcess: 4,
      }))
      .mockResolvedValueOnce(run('run-1', 'succeeded', {
        emailsMatched: 4, emailsProcessed: 4, emailsToProcess: 4,
      }));
    mailApi.getDigestTasks.mockResolvedValue([task('task-1')]);
    const snapshots: MailScanSnapshot[] = [];

    const pending = runMailScanProtocol({
      providers: ['gmail'],
      signal: new AbortController().signal,
      onProgress: (snapshot) => snapshots.push(snapshot),
    });
    await vi.advanceTimersByTimeAsync(3_000);
    const result = await pending;

    expect(snapshots.map((snapshot) => snapshot.progress.status)).toEqual([
      'connecting', 'queued', 'running', 'succeeded',
    ]);
    expect(snapshots.map((snapshot) => snapshot.terminal)).toEqual([false, false, false, true]);
    expect(result).toMatchObject({
      terminal: true,
      progress: { status: 'succeeded', emailsProcessed: 4, actionItemsCount: 1 },
    });
  });

  it('recovers after four consecutive poll errors and converts the fifth into a failed result', async () => {
    vi.useFakeTimers();
    mailApi.listConnections.mockResolvedValue({
      connections: [connection('gmail-1', 'gmail')],
      providerAvailability: {},
    });
    mailApi.createDigestRun.mockResolvedValue({ id: 'run-1', status: 'queued', statusUrl: '/status' });
    mailApi.getDigestTasks.mockResolvedValue([]);
    mailApi.getDigestRun
      .mockRejectedValueOnce(new Error('poll 1'))
      .mockRejectedValueOnce(new Error('poll 2'))
      .mockRejectedValueOnce(new Error('poll 3'))
      .mockRejectedValueOnce(new Error('poll 4'))
      .mockResolvedValueOnce(run('run-1', 'succeeded'));

    const recovered = runMailScanProtocol({
      providers: ['gmail'],
      signal: new AbortController().signal,
    });
    await vi.advanceTimersByTimeAsync(6_000);
    await expect(recovered).resolves.toMatchObject({ progress: { status: 'succeeded' } });
    expect(mailApi.getDigestRun).toHaveBeenCalledTimes(5);

    vi.clearAllMocks();
    mailApi.listConnections.mockResolvedValue({
      connections: [connection('gmail-1', 'gmail')],
      providerAvailability: {},
    });
    mailApi.createDigestRun.mockResolvedValue({ id: 'run-2', status: 'queued', statusUrl: '/status' });
    mailApi.getDigestRun.mockRejectedValue(new Error('poll unavailable'));

    const failed = runMailScanProtocol({
      providers: ['gmail'],
      signal: new AbortController().signal,
    });
    await vi.advanceTimersByTimeAsync(6_000);
    await expect(failed).resolves.toMatchObject({
      terminal: true,
      progress: { status: 'failed' },
      content: 'Gmail: poll unavailable',
    });
    expect(mailApi.getDigestRun).toHaveBeenCalledTimes(5);
  });

  it('propagates AbortError instead of converting cancellation into provider failure', async () => {
    vi.useFakeTimers();
    const abort = new AbortController();
    mailApi.listConnections.mockResolvedValue({
      connections: [connection('gmail-1', 'gmail')],
      providerAvailability: {},
    });
    mailApi.createDigestRun.mockResolvedValue({ id: 'run-1', status: 'queued', statusUrl: '/status' });
    mailApi.getDigestRun.mockResolvedValue(run('run-1', 'running'));

    const pending = runMailScanProtocol({ providers: ['gmail'], signal: abort.signal });
    await vi.advanceTimersByTimeAsync(0);
    abort.abort();

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('uses task count when available and run progress when task retrieval fails or is empty', async () => {
    mailApi.listConnections.mockResolvedValue({
      connections: [connection('gmail-1', 'gmail'), connection('outlook-1', 'outlook')],
      providerAvailability: {},
    });
    mailApi.createDigestRun.mockImplementation(async (input: { mailboxConnectionId: string }) => ({
      id: `${input.mailboxConnectionId}-run`, status: 'queued', statusUrl: '/status',
    }));
    mailApi.getDigestRun.mockImplementation(async (id: string) => run(id, 'succeeded', {
      emailsMatched: 1,
      emailsProcessed: 1,
      emailsToProcess: 1,
      actionItemsCount: id.startsWith('gmail') ? 3 : 7,
    }));
    mailApi.getDigestTasks.mockImplementation(async (id: string) => {
      if (id.startsWith('gmail')) throw new Error('tasks unavailable');
      return [task('task-1'), task('task-2')];
    });

    const result = await runMailScanProtocol({
      providers: ['gmail', 'outlook'],
      signal: new AbortController().signal,
    });

    expect(result.progress.actionItemsCount).toBe(5);
    expect(result.content).toContain('Gmail: Đã quét xong: đã quét 1 email và tạo 3 công việc.');
    expect(result.content).toContain('Outlook: Đã quét xong: đã quét 1 email và tạo 2 công việc.');
  });

  it('returns ordered partial content for Gmail success plus missing Outlook, and fails when all are unavailable', async () => {
    mailApi.listConnections.mockResolvedValue({
      connections: [connection('gmail-1', 'gmail')],
      providerAvailability: {},
    });
    mailApi.createDigestRun.mockResolvedValue({ id: 'gmail-run', status: 'queued', statusUrl: '/status' });
    mailApi.getDigestRun.mockResolvedValue(run('gmail-run', 'succeeded', {
      emailsMatched: 2, emailsProcessed: 2, emailsToProcess: 2,
    }));
    mailApi.getDigestTasks.mockResolvedValue([task('task-1')]);

    const partial = await runMailScanProtocol({
      providers: ['gmail', 'outlook'],
      signal: new AbortController().signal,
    });

    expect(partial).toMatchObject({ terminal: true, progress: { status: 'partial' } });
    expect(partial.content.split('\n')).toEqual([
      'Gmail: Đã quét xong: đã quét 2 email và tạo 1 công việc.',
      expect.stringContaining('Outlook: Chưa có tài khoản Outlook'),
    ]);

    vi.clearAllMocks();
    mailApi.listConnections.mockResolvedValue({ connections: [], providerAvailability: {} });
    const failed = await runMailScanProtocol({
      providers: ['outlook', 'gmail'],
      signal: new AbortController().signal,
    });
    expect(failed).toMatchObject({ terminal: true, progress: { status: 'failed' } });
    expect(failed.content.split('\n')[0]).toContain('Outlook: Chưa có tài khoản Outlook');
    expect(failed.content.split('\n')[1]).toContain('Gmail: Chưa có tài khoản Gmail');
  });

  it('runs providers concurrently, retains input order, and sums provider totals', async () => {
    mailApi.listConnections.mockResolvedValue({
      connections: [connection('gmail-1', 'gmail'), connection('outlook-1', 'outlook')],
      providerAvailability: {},
    });
    mailApi.createDigestRun.mockImplementation(async (input: { mailboxConnectionId: string }) => ({
      id: `${input.mailboxConnectionId}-run`, status: 'queued', statusUrl: '/status',
    }));
    let releaseGmail!: (value: DigestRunView) => void;
    const gmailRun = new Promise<DigestRunView>((resolve) => { releaseGmail = resolve; });
    mailApi.getDigestRun.mockImplementation((id: string) => id.startsWith('gmail')
      ? gmailRun
      : Promise.resolve(run(id, 'succeeded', {
        emailsMatched: 3, emailsProcessed: 2, emailsToProcess: 3,
      }))
    );
    mailApi.getDigestTasks.mockResolvedValue([]);
    const snapshots: MailScanSnapshot[] = [];

    const pending = runMailScanProtocol({
      providers: ['gmail', 'outlook'],
      signal: new AbortController().signal,
      onProgress: (snapshot) => snapshots.push(snapshot),
    });
    await vi.waitFor(() => expect(mailApi.getDigestRun).toHaveBeenCalledTimes(2));
    expect(snapshots.at(-1)?.content.split('\n')).toEqual([
      expect.stringContaining('Gmail:'),
      expect.stringContaining('Outlook:'),
    ]);
    releaseGmail(run('gmail-1-run', 'succeeded', {
      emailsMatched: 5, emailsProcessed: 4, emailsToProcess: 5,
    }));

    const result = await pending;
    expect(result.content.split('\n')).toEqual([
      expect.stringContaining('Gmail:'),
      expect.stringContaining('Outlook:'),
    ]);
    expect(result.progress).toMatchObject({
      emailsMatched: 8,
      emailsProcessed: 6,
      emailsToProcess: 8,
    });
  });
});
