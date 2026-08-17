import React, { useCallback, useEffect, useMemo, useState } from 'react';
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
  disconnectConnection,
  getDigestResult,
  getDigestTasks,
  getGmailConnectUrl,
  listConnections,
  listDigestRuns,
  type DigestResult,
  type DigestRunView,
  type DigestTask,
  type MailboxConnection,
} from '../../modules/mail/api';

function errorMessage(error: unknown): string {
  if (error instanceof MailApiError) {
    if (error.status === 409) return `${error.message} Hãy kết nối lại Gmail nếu cần.`;
    if (error.status === 503) return `${error.message} Vui lòng thử lại sau.`;
    return error.message;
  }
  return error instanceof Error ? error.message : 'Đã xảy ra lỗi không xác định.';
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
  const [selectedRun, setSelectedRun] = useState<DigestRunView | null>(null);
  const [result, setResult] = useState<DigestResult | null>(null);
  const [tasks, setTasks] = useState<DigestTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [loadingMailbox, setLoadingMailbox] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(initialOAuthBanner);

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

  const loadMailbox = useCallback(
    async (connectionId: string, signal?: AbortSignal) => {
      setLoadingMailbox(true);
      setError(null);
      try {
        const runs = await listDigestRuns(connectionId, 20, signal);
        const latestCompletedRun = runs.find(
          (run) => run.status === 'succeeded' || run.status === 'partial'
        );
        setSelectedRun(latestCompletedRun ?? null);
        setResult(null);
        setTasks([]);
        setSelectedTaskId(null);
        if (latestCompletedRun) await loadOutputs(latestCompletedRun.id, signal);
      } catch (cause) {
        if ((cause as { name?: string }).name !== 'AbortError') setError(errorMessage(cause));
      } finally {
        setLoadingMailbox(false);
      }
    },
    [loadOutputs]
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
      await refreshConnections();
    } catch (cause) {
      setError(errorMessage(cause));
    }
  };

  const handleConnectionChange = (connectionId: string) => {
    setSelectedRun(null);
    setResult(null);
    setTasks([]);
    setSelectedTaskId(null);
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
              {!result && tasks.length === 0 && !loadingMailbox && (
                <p className="rounded-xl border border-dashed border-zinc-700 p-8 text-center text-sm text-zinc-500">
                  Chưa có action item từ lần quét gần nhất.
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
