import {
  createDigestRun,
  getDigestRun,
  getDigestTasks,
  getSelectedMailboxId,
  listConnections,
  newIdempotencyKey,
  setSelectedMailboxId,
  type DigestRunView,
  type DigestTask,
  type MailProvider,
  type MailboxConnection,
} from '../../modules/mail/api';
import type { MailScanProgress } from '../types';

const MAIL_UNREAD_QUERY = 'is:unread in:inbox category:primary';
const MAIL_SCAN_MAX_EMAILS = 10;
const MAIL_POLL_INTERVAL_MS = 1_500;
const MAIL_TERMINAL_STATUSES = new Set(['succeeded', 'partial', 'failed']);

export interface MailScanSnapshot {
  content: string;
  progress: MailScanProgress;
  terminal: boolean;
}

interface ProviderOutcome {
  provider: MailProvider;
  content: string;
  progress: MailScanProgress;
}

function providerLabel(provider: MailProvider): string {
  return provider === 'gmail' ? 'Gmail' : 'Outlook';
}

function progressFromRun(run: DigestRunView): MailScanProgress {
  return {
    status: run.status,
    emailsMatched: run.progress.emailsMatched,
    emailsProcessed: run.progress.emailsProcessed,
    emailsToProcess: run.progress.emailsToProcess,
  };
}

function waitForPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, MAIL_POLL_INTERVAL_MS);
    signal.addEventListener('abort', () => {
      window.clearTimeout(timer);
      reject(new DOMException('Mail scan polling aborted', 'AbortError'));
    }, { once: true });
  });
}

function isAbortError(error: unknown): boolean {
  return (error as { name?: string }).name === 'AbortError';
}

export async function runMailScanProtocol(input: {
  providers: readonly MailProvider[];
  signal: AbortSignal;
  onProgress?: (snapshot: MailScanSnapshot) => void;
}): Promise<MailScanSnapshot> {
  const states = new Map<MailProvider, ProviderOutcome>();

  const aggregate = (): MailScanProgress => {
    const values = input.providers.map((provider) => states.get(provider)).filter(
      (value): value is ProviderOutcome => Boolean(value)
    );
    const terminal = values.length === input.providers.length && values.every(
      (value) => MAIL_TERMINAL_STATUSES.has(value.progress.status)
    );
    let status: MailScanProgress['status'];
    if (terminal) {
      const usable = values.filter((value) =>
        value.progress.status === 'succeeded' || value.progress.status === 'partial'
      );
      status = usable.length === 0
        ? 'failed'
        : values.every((value) => value.progress.status === 'succeeded') ? 'succeeded' : 'partial';
    } else if (values.some((value) => value.progress.status === 'running')) {
      status = 'running';
    } else if (values.some((value) => value.progress.status === 'queued')) {
      status = 'queued';
    } else {
      status = 'connecting';
    }
    return {
      status,
      emailsMatched: values.reduce((sum, value) => sum + value.progress.emailsMatched, 0),
      emailsProcessed: values.reduce((sum, value) => sum + value.progress.emailsProcessed, 0),
      emailsToProcess: values.reduce((sum, value) => sum + value.progress.emailsToProcess, 0),
      actionItemsCount: values.reduce(
        (sum, value) => sum + (value.progress.actionItemsCount ?? 0), 0
      ),
    };
  };

  const snapshot = (): MailScanSnapshot => {
    const progress = aggregate();
    return {
      content: input.providers.map((provider) => {
        const state = states.get(provider);
        return `${providerLabel(provider)}: ${state?.content ?? 'Đang chuẩn bị…'}`;
      }).join('\n'),
      progress,
      terminal: MAIL_TERMINAL_STATUSES.has(progress.status),
    };
  };

  const publish = (provider: MailProvider, outcome: ProviderOutcome): MailScanSnapshot => {
    states.set(provider, outcome);
    const next = snapshot();
    input.onProgress?.(next);
    return next;
  };

  const listed = await listConnections(input.signal);
  const activeConnections = listed.connections.filter(
    (connection) => connection.status === 'active'
  );

  const selectedConnection = (provider: MailProvider): MailboxConnection | undefined => {
    const candidates = activeConnections.filter((connection) => connection.provider === provider);
    const remembered = getSelectedMailboxId(provider);
    const selected = candidates.find((connection) => connection.id === remembered) ?? candidates[0];
    if (selected) setSelectedMailboxId(provider, selected.id);
    return selected;
  };

  const scanProvider = async (provider: MailProvider): Promise<ProviderOutcome> => {
    const connection = selectedConnection(provider);
    if (!connection) {
      return {
        provider,
        content: `Chưa có tài khoản ${providerLabel(provider)} đang kết nối. Hãy mở Mail Inbox để kết nối ${providerLabel(provider)}.`,
        progress: { status: 'failed', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0 },
      };
    }
    publish(provider, {
      provider,
      content: 'Đang tạo lượt quét 10 email unread mới nhất…',
      progress: { status: 'connecting', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0 },
    });
    try {
      const accepted = await createDigestRun({
        mailboxConnectionId: connection.id,
        maxEmails: MAIL_SCAN_MAX_EMAILS,
        query: provider === 'gmail' ? MAIL_UNREAD_QUERY : undefined,
        idempotencyKey: newIdempotencyKey(),
        signal: input.signal,
      });
      let consecutiveErrors = 0;
      while (!input.signal.aborted) {
        let run: DigestRunView;
        try {
          run = await getDigestRun(accepted.id, input.signal);
          consecutiveErrors = 0;
        } catch (error) {
          if (isAbortError(error)) throw error;
          consecutiveErrors += 1;
          if (consecutiveErrors >= 5) throw error;
          await waitForPoll(input.signal);
          continue;
        }
        const progress = progressFromRun(run);
        if (!MAIL_TERMINAL_STATUSES.has(run.status)) {
          publish(provider, { provider, content: 'Đang quét email unread mới nhất…', progress });
          await waitForPoll(input.signal);
          continue;
        }
        if (run.status === 'failed') {
          return {
            provider,
            content: run.error?.message ?? 'Không thể hoàn tất lượt quét email.',
            progress,
          };
        }
        let tasks: DigestTask[] = [];
        try {
          tasks = await getDigestTasks(run.id, input.signal);
        } catch (error) {
          if (isAbortError(error)) throw error;
        }
        const finalCount = tasks.length || run.progress.actionItemsCount || 0;
        const resultLabel = run.status === 'partial' ? 'Hoàn tất một phần' : 'Đã quét xong';
        return {
          provider,
          content: [
            `${resultLabel}: đã quét ${run.progress.emailsProcessed} email và tạo ${finalCount} công việc.`,
            run.progress.filteredSummary?.trim(),
          ].filter(Boolean).join(' '),
          progress: { ...progress, actionItemsCount: finalCount },
        };
      }
      throw new DOMException('Mail scan polling aborted', 'AbortError');
    } catch (error) {
      if (isAbortError(error)) throw error;
      return {
        provider,
        content: error instanceof Error ? error.message : 'Không thể hoàn tất lượt quét email.',
        progress: { status: 'failed', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0 },
      };
    }
  };

  const outcomes = await Promise.all(input.providers.map(async (provider) => {
    const outcome = await scanProvider(provider);
    publish(provider, outcome);
    return outcome;
  }));
  for (const outcome of outcomes) states.set(outcome.provider, outcome);
  return snapshot();
}
