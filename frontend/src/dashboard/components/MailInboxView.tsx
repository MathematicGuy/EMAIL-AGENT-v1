import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ExternalLink,
  Link2,
  LogOut,
  Mail,
} from 'lucide-react';
import {
  MailApiError,
  createDigestRun,
  disconnectConnection,
  getDigestResult,
  getDigestRun,
  getDigestTasks,
  getGmailConnectUrl,
  listConnections,
  listDigestRuns,
  newIdempotencyKey,
  type DigestResult,
  type DigestRunHistoryItem,
  type DigestRunStatus,
  type DigestRunView,
  type DigestTask,
  type MailboxConnection,
} from '../../modules/mail/api';

const POLL_INTERVAL_MS = 1_500;
const POLL_TIMEOUT_MS = 30 * 60 * 1_000;
const TERMINAL_STATUSES = new Set<DigestRunStatus>(['succeeded', 'partial', 'failed']);

function waitForPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, POLL_INTERVAL_MS);
    signal.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer);
        reject(new DOMException('Polling aborted', 'AbortError'));
      },
      { once: true }
    );
  });
}

function errorMessage(error: unknown): string {
  if (error instanceof MailApiError) {
    if (error.status === 409) return `${error.message} Hãy kết nối lại Gmail nếu cần.`;
    if (error.status === 503) return `${error.message} Vui lòng thử lại sau.`;
    return error.message;
  }
  return error instanceof Error ? error.message : 'Đã xảy ra lỗi không xác định.';
}

function statusLabel(status: DigestRunStatus): string {
  return {
    queued: 'Đang chờ',
    running: 'Đang xử lý',
    succeeded: 'Hoàn tất',
    partial: 'Hoàn tất một phần',
    failed: 'Thất bại',
  }[status];
}

function statusClass(status: DigestRunStatus): string {
  if (status === 'succeeded') return 'text-emerald-300 bg-emerald-500/10';
  if (status === 'partial') return 'text-amber-300 bg-amber-500/10';
  if (status === 'failed') return 'text-red-300 bg-red-500/10';
  return 'text-sky-300 bg-sky-500/10';
}

function initialOAuthBanner(): string | null {
  const outcome = new URLSearchParams(window.location.search).get('gmail');
  if (outcome === 'connected') return 'Đã kết nối Gmail thành công.';
  if (outcome === 'denied') return 'Bạn đã từ chối quyền kết nối Gmail.';
  if (outcome === 'error') return 'Không thể hoàn tất kết nối Gmail. Vui lòng thử lại.';
  return null;
}

export const MailInboxView: React.FC = () => {
  const [connections, setConnections] = useState<MailboxConnection[]>([]);
  const [selectedConnectionId, setSelectedConnectionId] = useState('');
  const [history, setHistory] = useState<DigestRunHistoryItem[]>([]);
  const [selectedRun, setSelectedRun] = useState<DigestRunView | null>(null);
  const [result, setResult] = useState<DigestResult | null>(null);
  const [tasks, setTasks] = useState<DigestTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [maxEmails, setMaxEmails] = useState(20);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [loadingMailbox, setLoadingMailbox] = useState(false);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(initialOAuthBanner);
  const pollControllerRef = useRef<AbortController | null>(null);
  const pendingIdempotencyKeyRef = useRef<string | null>(null);

  const selectedConnection = useMemo(
    () => connections.find((connection) => connection.id === selectedConnectionId) ?? null,
    [connections, selectedConnectionId]
  );
  const selectedTask = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? null,
    [selectedTaskId, tasks]
  );
  const hasActiveRun =
    selectedRun?.status === 'queued' || selectedRun?.status === 'running';

  const refreshConnections = useCallback(async (signal?: AbortSignal) => {
    setLoadingConnections(true);
    try {
      const loaded = await listConnections(signal);
      const active = loaded.filter((connection) => connection.status === 'active');
      setConnections(active);
      setSelectedConnectionId((current) =>
        active.some((connection) => connection.id === current) ? current : (active[0]?.id ?? '')
      );
    } catch (cause) {
      if ((cause as { name?: string }).name !== 'AbortError') setError(errorMessage(cause));
    } finally {
      setLoadingConnections(false);
    }
  }, []);

  const refreshHistory = useCallback(async (connectionId: string, signal?: AbortSignal) => {
    const runs = await listDigestRuns(connectionId, 20, signal);
    setHistory(runs);
  }, []);

  const loadMailbox = useCallback(
    async (connectionId: string, signal?: AbortSignal) => {
      setLoadingMailbox(true);
      setError(null);
      try {
        const runs = await listDigestRuns(connectionId, 20, signal);
        setHistory(runs);
      } catch (cause) {
        if ((cause as { name?: string }).name !== 'AbortError') setError(errorMessage(cause));
      } finally {
        setLoadingMailbox(false);
      }
    },
    []
  );

  const loadOutputs = useCallback(async (runId: string, signal?: AbortSignal) => {
    const [nextResult, nextTasks] = await Promise.all([
      getDigestResult(runId, signal),
      getDigestTasks(runId, signal),
    ]);
    setResult(nextResult);
    setTasks(nextTasks);
    setSelectedTaskId((current) =>
      nextTasks.some((task) => task.task_id === current)
        ? current
        : (nextTasks[0]?.task_id ?? null)
    );
  }, []);

  const pollRun = useCallback(
    async (runId: string, connectionId: string, controller: AbortController) => {
      const startedAt = Date.now();
      setPolling(true);
      setError(null);
      try {
        while (!controller.signal.aborted) {
          const run = await getDigestRun(runId, controller.signal);
          setSelectedRun(run);
          if (TERMINAL_STATUSES.has(run.status)) {
            if (run.status !== 'failed') await loadOutputs(run.id, controller.signal);
            else {
              setResult(null);
              setTasks([]);
              setError(run.error?.message ?? 'Run xử lý email thất bại.');
            }
            await refreshHistory(connectionId, controller.signal);
            return;
          }
          if (Date.now() - startedAt >= POLL_TIMEOUT_MS) {
            setBanner('Run vẫn có thể đang tiếp tục. Hãy chọn lại run trong lịch sử để theo dõi.');
            await refreshHistory(connectionId, controller.signal);
            return;
          }
          await waitForPoll(controller.signal);
        }
      } catch (cause) {
        if ((cause as { name?: string }).name !== 'AbortError') setError(errorMessage(cause));
      } finally {
        setPolling(false);
      }
    },
    [loadOutputs, refreshHistory]
  );

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams(window.location.search);
    const oauthOutcome = params.get('gmail');
    if (oauthOutcome) {
      params.delete('gmail');
      const query = params.toString();
      window.history.replaceState(null, '', `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`);
    }
    const timer = window.setTimeout(() => void refreshConnections(controller.signal), 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [refreshConnections]);

  useEffect(() => {
    pollControllerRef.current?.abort();
    if (!selectedConnectionId) return;
    const controller = new AbortController();
    const timer = window.setTimeout(
      () => void loadMailbox(selectedConnectionId, controller.signal),
      0
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [loadMailbox, selectedConnectionId]);

  useEffect(() => () => pollControllerRef.current?.abort(), []);

  const handleScan = async () => {
    if (!selectedConnectionId || polling) return;
    const controller = new AbortController();
    pollControllerRef.current?.abort();
    pollControllerRef.current = controller;
    setResult(null);
    setTasks([]);
    setSelectedTaskId(null);
    setBanner(null);
    setError(null);
    const idempotencyKey = pendingIdempotencyKeyRef.current ?? newIdempotencyKey();
    pendingIdempotencyKeyRef.current = idempotencyKey;
    try {
      const accepted = await createDigestRun({
        mailboxConnectionId: selectedConnectionId,
        maxEmails,
        idempotencyKey,
        signal: controller.signal,
      });
      pendingIdempotencyKeyRef.current = null;
      await pollRun(accepted.id, selectedConnectionId, controller);
      await loadMailbox(selectedConnectionId, controller.signal);
    } catch (cause) {
      if ((cause as { name?: string }).name !== 'AbortError') setError(errorMessage(cause));
    }
  };

  const handleSelectRun = async (item: DigestRunHistoryItem) => {
    if (!selectedConnectionId) return;
    pollControllerRef.current?.abort();
    const controller = new AbortController();
    pollControllerRef.current = controller;
    setSelectedRun(item);
    setResult(null);
    setTasks([]);
    setSelectedTaskId(null);
    setError(null);
    if (item.status === 'queued' || item.status === 'running') {
      await pollRun(item.id, selectedConnectionId, controller);
      return;
    }
    if (item.status === 'failed') {
      setError(item.error?.message ?? 'Run xử lý email thất bại.');
      return;
    }
    try {
      await loadOutputs(item.id, controller.signal);
    } catch (cause) {
      if ((cause as { name?: string }).name !== 'AbortError') setError(errorMessage(cause));
    }
  };

  const handleDisconnect = async () => {
    if (!selectedConnection || hasActiveRun) return;
    if (!window.confirm(`Ngắt kết nối ${selectedConnection.emailAddress}?`)) return;
    setError(null);
    try {
      await disconnectConnection(selectedConnection.id);
      setBanner(`Đã ngắt kết nối ${selectedConnection.emailAddress}.`);
      setSelectedRun(null);
      setResult(null);
      setTasks([]);
      setSelectedTaskId(null);
      setHistory([]);
      await refreshConnections();
    } catch (cause) {
      setError(errorMessage(cause));
    }
  };

  const progress = selectedRun?.progress;
  const progressPercent = progress
    ? Math.min(100, Math.round((progress.emailsProcessed / Math.max(progress.emailsToProcess, 1)) * 100))
    : 0;

  const handleConnectionChange = (connectionId: string) => {
    pollControllerRef.current?.abort();
    pendingIdempotencyKeyRef.current = null;
    setSelectedRun(null);
    setResult(null);
    setTasks([]);
    setSelectedTaskId(null);
    setHistory([]);
    setSelectedConnectionId(connectionId);
  };

  return (
    <section className="flex-1 overflow-auto bg-[#1b1a17] p-5 md:p-8">
      <div className="mx-auto max-w-6xl space-y-5">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-xl font-semibold">
              <Mail className="h-5 w-5 text-[#d97757]" /> Mail Inbox
            </h1>
            <p className="mt-1 text-sm text-zinc-400">Gmail chỉ đọc · nội dung email không được lưu.</p>
          </div>
          {connections.length > 0 && (
            <div className="flex items-center gap-2">
              <select
                aria-label="Tài khoản Gmail"
                value={selectedConnectionId}
                onChange={(event) => handleConnectionChange(event.target.value)}
                className="rounded-lg border border-zinc-700 bg-[#242320] px-3 py-2 text-sm"
              >
                {connections.map((connection) => (
                  <option key={connection.id} value={connection.id}>{connection.emailAddress}</option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => void handleDisconnect()}
                disabled={hasActiveRun}
                title={hasActiveRun ? 'Không thể ngắt kết nối khi run đang chạy' : 'Ngắt kết nối'}
                className="rounded-lg border border-zinc-700 p-2 text-zinc-400 hover:text-red-300 disabled:opacity-40"
              >
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          )}
        </header>

        {banner && <p role="status" className="rounded-lg border border-sky-800 bg-sky-950/40 px-4 py-3 text-sm text-sky-200">{banner}</p>}
        {error && <p role="alert" className="rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-200">{error}</p>}

        {!loadingConnections && connections.length === 0 && (
          <div className="rounded-xl border border-zinc-700 bg-[#242320] p-10 text-center">
            <Link2 className="mx-auto mb-3 h-8 w-8 text-[#d97757]" />
            <h2 className="font-medium">Chưa có tài khoản Gmail</h2>
            <p className="mt-2 text-sm text-zinc-400">Kết nối bằng quyền gmail.readonly để bắt đầu.</p>
            <a href={getGmailConnectUrl()} className="mt-5 inline-block rounded-lg bg-[#d97757] px-4 py-2 text-sm font-medium text-white">Kết nối Gmail</a>
          </div>
        )}

        {selectedConnection && (
          <div className="space-y-6">
            <section className="rounded-2xl border border-zinc-700 bg-[#22211e] p-4 md:p-5">
              <div className="grid items-end gap-4 md:grid-cols-[minmax(180px,1fr)_minmax(220px,1.4fr)_auto]">
                <div>
                  <label htmlFor="run-history" className="mb-2 block text-xs text-zinc-400">
                    Lần quét
                  </label>
                  <select
                    id="run-history"
                    value={selectedRun?.id ?? ''}
                    onChange={(event) => {
                      const run = history.find((item) => item.id === event.target.value);
                      if (run) void handleSelectRun(run);
                    }}
                    className="w-full rounded-lg border border-zinc-700 bg-[#191815] px-3 py-2.5 text-sm"
                  >
                    <option value="">Chọn lịch sử quét</option>
                    {history.map((run) => (
                      <option key={run.id} value={run.id}>
                        {run.createdAt ? new Date(run.createdAt).toLocaleString('vi-VN') : run.id}
                        {' · '}{statusLabel(run.status)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <div className="mb-2 flex items-center justify-between text-xs text-zinc-400">
                    <label htmlFor="max-emails">Số email cần quét</label>
                    <span>{maxEmails} email</span>
                  </div>
                  <input
                    id="max-emails"
                    type="range"
                    min="5"
                    max="100"
                    step="5"
                    value={maxEmails}
                    onChange={(event) => setMaxEmails(Number(event.target.value))}
                    className="h-2 w-full accent-[#d97757]"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => void handleScan()}
                  disabled={polling || loadingMailbox}
                  className="rounded-lg bg-[#d97757] px-6 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
                >
                  {polling ? 'Đang xử lý…' : 'Quét mail mới'}
                </button>
              </div>
              {selectedRun && (
                <div className="mt-4 space-y-2 border-t border-zinc-700/70 pt-4">
                  <div className="flex items-center justify-between text-xs">
                    <span className={`rounded px-2 py-1 ${statusClass(selectedRun.status)}`}>
                      {statusLabel(selectedRun.status)}
                    </span>
                    <span className="text-zinc-400">
                      {progress?.emailsProcessed ?? 0}/{progress?.emailsToProcess ?? 0} email
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded bg-zinc-800">
                    <div
                      className="h-full bg-[#d97757] transition-all"
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>
                </div>
              )}
            </section>

            <section className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-2xl font-semibold">Danh mục hành động ({tasks.length})</h2>
                {selectedRun?.status === 'partial' && (
                  <span className="flex items-center gap-1 rounded bg-amber-500/10 px-2 py-1 text-xs text-amber-300">
                    <AlertTriangle className="h-3 w-3" /> Kết quả một phần
                  </span>
                )}
              </div>

              {result?.message && tasks.length === 0 && (
                <p className="rounded-xl border border-zinc-700 bg-[#22211e] p-6 text-sm text-zinc-400">
                  {result.message}
                </p>
              )}
              {!result && tasks.length === 0 && !polling && (
                <p className="rounded-xl border border-dashed border-zinc-700 p-8 text-center text-sm text-zinc-500">
                  Quét email hoặc chọn một lần quét cũ để xem danh mục hành động.
                </p>
              )}
              {result?.attachmentWarnings.map((warning) => (
                <p key={`${warning.filename}-${warning.code}`} className="rounded-lg border border-amber-900 bg-amber-950/30 p-3 text-xs text-amber-200">
                  {warning.filename}: {warning.message}
                </p>
              ))}

              {tasks.length > 0 && (
                <div className="mx-auto max-w-4xl space-y-4">
                  {tasks.map((task) => {
                    const selected = selectedTask?.task_id === task.task_id;
                    return (
                      <article
                        key={task.task_id}
                        className={`overflow-hidden rounded-xl border transition ${
                          selected
                            ? 'border-[#d97757]/70 bg-[#24211d]'
                            : 'border-zinc-700 bg-[#22211e] hover:border-zinc-500'
                        }`}
                      >
                        <button
                          type="button"
                          aria-expanded={selected}
                          aria-controls={`action-plan-${task.task_id}`}
                          onClick={() => setSelectedTaskId(selected ? null : task.task_id)}
                          className="group w-full p-5 text-left md:p-6"
                        >
                          <div className="flex items-start gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-start justify-between gap-3">
                                <h3 className="text-base font-semibold leading-snug md:text-lg">
                                  {task.title}
                                </h3>
                                <div className="flex shrink-0 flex-wrap gap-2 text-xs">
                                  <span className="rounded-full bg-[#d97757]/15 px-2.5 py-1 text-[#e8a78f]">
                                    {task.priority ?? 'unknown'}
                                  </span>
                                  <span className="rounded-full bg-zinc-800 px-2.5 py-1 text-zinc-300">
                                    {task.route}
                                  </span>
                                </div>
                              </div>
                              <p className="mt-1.5 text-sm leading-relaxed text-zinc-400">
                                {task.request_summary}
                              </p>
                              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-zinc-500">
                                {task.deadline && (
                                  <span>
                                    Hạn: {new Date(task.deadline).toLocaleString('vi-VN')}
                                  </span>
                                )}
                                <span>{task.source_message_ids.length} email nguồn</span>
                              </div>
                            </div>
                            <ChevronDown
                              className={`mt-1 h-4 w-4 shrink-0 text-zinc-500 transition-transform ${
                                selected ? 'rotate-180 text-[#e8a78f]' : ''
                              }`}
                            />
                          </div>
                        </button>

                        {selected && (
                          <div
                            id={`action-plan-${task.task_id}`}
                            className="border-t border-zinc-700/70 bg-[#1d1b18] px-5 py-5 md:px-14 md:py-6"
                          >
                            <p className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
                              Kế hoạch chi tiết
                            </p>

                            {task.action_plan.length > 0 ? (
                              <ol className="mt-4 space-y-3">
                                {task.action_plan.map((step) => (
                                  <li key={step.step} className="flex gap-3 text-sm leading-relaxed">
                                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-zinc-600 bg-zinc-800 text-xs text-zinc-300">
                                      {step.step}
                                    </span>
                                    <span className="pt-0.5">{step.instruction}</span>
                                  </li>
                                ))}
                              </ol>
                            ) : (
                              <p className="mt-4 text-sm text-zinc-500">
                                Action Item này chưa có bước thực hiện.
                              </p>
                            )}

                            {task.missing_information.length > 0 && (
                              <div className="mt-5 rounded-lg border border-amber-800/70 bg-amber-950/20 p-3 text-sm text-amber-200">
                                <strong>Thiếu thông tin:</strong>{' '}
                                {task.missing_information.join('; ')}
                              </div>
                            )}

                            <div className="mt-5 flex flex-wrap gap-4 text-sm">
                              <a
                                href={task.gmail_url}
                                target="_blank"
                                rel="noreferrer"
                                className="flex items-center gap-1 text-[#e8a78f] hover:text-[#f2b9a4]"
                                onClick={(event) => event.stopPropagation()}
                              >
                                <ExternalLink className="h-3.5 w-3.5" /> Mở email nguồn (
                                {task.source_message_ids.length})
                              </a>
                              {task.supporting_documents.map((document) => (
                                <a
                                  key={document.citation_id}
                                  href={document.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-sky-300 hover:text-sky-200"
                                >
                                  {document.title}
                                </a>
                              ))}
                            </div>
                          </div>
                        )}
                      </article>
                    );
                  })}
                </div>
              )}
            </section>

          </div>
        )}
      </div>
    </section>
  );
};
