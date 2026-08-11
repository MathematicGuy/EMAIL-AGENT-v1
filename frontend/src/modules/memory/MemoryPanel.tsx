import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  BrainCircuit,
  Database,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  X
} from 'lucide-react';
import {
  confirmMemoryClaim,
  correctMemoryClaim,
  deleteMemory,
  forgetMemoryClaim,
  getMemoryClaimSources,
  getProjectMemorySummary,
  listMemories,
  listMemoryClaims,
  MemoryApiError,
  previewMemoryContext,
  refreshProjectMemorySummary,
  resolveContextSnapshot,
  updateMemory
} from './api';
import type {
  ContextSnapshot,
  MemoryClaim,
  MemoryClaimSource,
  MemoryContextPreview,
  MemoryRecord,
  ProjectMemorySummary
} from './types';

interface MemoryPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

const DEMO_SCOPE = {
  actorId: 'demo-user',
  projectId: 'demo-project',
  workspaceId: 'demo-workspace'
};

export function MemoryPanel({ isOpen, onClose }: MemoryPanelProps) {
  const [tab, setTab] = useState<'project' | 'memories' | 'snapshot'>('memories');
  const [items, setItems] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [snapshotId, setSnapshotId] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [jobId, setJobId] = useState('');
  const [snapshot, setSnapshot] = useState<ContextSnapshot | null>(null);
  const [summary, setSummary] = useState<ProjectMemorySummary | null>(null);
  const [claims, setClaims] = useState<MemoryClaim[]>([]);
  const [claimSources, setClaimSources] = useState<
    Record<string, MemoryClaimSource[]>
  >({});
  const [claimDraft, setClaimDraft] = useState('');
  const [editingClaimId, setEditingClaimId] = useState<string | null>(null);
  const [previewQuery, setPreviewQuery] = useState('');
  const [preview, setPreview] = useState<MemoryContextPreview | null>(null);

  const load = async (searchQuery = query) => {
    setLoading(true);
    setError(null);
    try {
      const result = await listMemories({ ...DEMO_SCOPE, query: searchQuery });
      setItems(result.items);
    } catch (requestError) {
      setError(
        requestError instanceof MemoryApiError
          ? `${requestError.code}: ${requestError.message}`
          : 'Không thể tải memory.'
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    listMemories({ ...DEMO_SCOPE, query: '' })
      .then((result) => {
        if (!cancelled) setItems(result.items);
      })
      .catch((requestError: unknown) => {
        if (!cancelled) {
          setError(
            requestError instanceof MemoryApiError
              ? `${requestError.code}: ${requestError.message}`
              : 'Không thể tải memory.'
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const saveEdit = async (memory: MemoryRecord) => {
    if (!draft.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const updated = await updateMemory({
        memory,
        content: draft.trim(),
        ...DEMO_SCOPE
      });
      setItems((current) =>
        current.map((item) =>
          item.memory_id === updated.memory_id ? updated : item
        )
      );
      setEditingId(null);
    } catch (requestError) {
      setError(
        requestError instanceof MemoryApiError
          ? `${requestError.code}: ${requestError.message}`
          : 'Không thể cập nhật memory.'
      );
    } finally {
      setLoading(false);
    }
  };

  const confirmDelete = async (memory: MemoryRecord) => {
    setLoading(true);
    setError(null);
    try {
      const deleted = await deleteMemory({ memory, ...DEMO_SCOPE });
      setItems((current) =>
        current.map((item) =>
          item.memory_id === deleted.memory_id ? deleted : item
        )
      );
      setDeletingId(null);
    } catch (requestError) {
      setError(
        requestError instanceof MemoryApiError
          ? `${requestError.code}: ${requestError.message}`
          : 'Không thể vô hiệu hóa memory.'
      );
    } finally {
      setLoading(false);
    }
  };

  const inspectSnapshot = async () => {
    if (!snapshotId.trim() || !accessToken || !jobId.trim()) return;
    setLoading(true);
    setError(null);
    setSnapshot(null);
    try {
      setSnapshot(
        await resolveContextSnapshot({
          snapshotId: snapshotId.trim(),
          accessToken,
          serviceIdentity: 'module-2-executor',
          jobId: jobId.trim(),
          attempt: 1,
          operationRevision: 1
        })
      );
      setAccessToken('');
    } catch (requestError) {
      setError(
        requestError instanceof MemoryApiError
          ? `${requestError.code}: ${requestError.message}`
          : 'Không thể resolve snapshot.'
      );
    } finally {
      setLoading(false);
    }
  };

  const setRequestError = (requestError: unknown, fallback: string) => {
    setError(
      requestError instanceof MemoryApiError
        ? `${requestError.code}: ${requestError.message}`
        : fallback
    );
  };

  const loadProjectMemory = async () => {
    setLoading(true);
    setError(null);
    try {
      const [summaryResult, claimsResult] = await Promise.all([
        getProjectMemorySummary(DEMO_SCOPE),
        listMemoryClaims(DEMO_SCOPE)
      ]);
      setSummary(summaryResult);
      setClaims(claimsResult.items);
    } catch (requestError) {
      setRequestError(requestError, 'Không thể tải Project Memory.');
    } finally {
      setLoading(false);
    }
  };

  const refreshProjectMemory = async () => {
    setLoading(true);
    setError(null);
    try {
      const summaryResult = await refreshProjectMemorySummary(DEMO_SCOPE);
      const claimsResult = await listMemoryClaims(DEMO_SCOPE);
      setSummary(summaryResult);
      setClaims(claimsResult.items);
    } catch (requestError) {
      setRequestError(requestError, 'Không thể chạy LLM Memory.');
    } finally {
      setLoading(false);
    }
  };

  const replaceClaim = (updated: MemoryClaim, priorId = updated.claim_id) => {
    setClaims((current) => [
      updated,
      ...current.filter((item) => item.claim_id !== priorId)
    ]);
  };

  const confirmClaim = async (claim: MemoryClaim) => {
    setLoading(true);
    setError(null);
    try {
      replaceClaim(await confirmMemoryClaim({ claim, ...DEMO_SCOPE }));
    } catch (requestError) {
      setRequestError(requestError, 'Không thể xác nhận memory.');
    } finally {
      setLoading(false);
    }
  };

  const saveClaimCorrection = async (claim: MemoryClaim) => {
    if (!claimDraft.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const updated = await correctMemoryClaim({
        claim,
        content: claimDraft.trim(),
        ...DEMO_SCOPE
      });
      replaceClaim(updated, claim.claim_id);
      setEditingClaimId(null);
    } catch (requestError) {
      setRequestError(requestError, 'Không thể sửa memory.');
    } finally {
      setLoading(false);
    }
  };

  const forgetClaim = async (claim: MemoryClaim) => {
    setLoading(true);
    setError(null);
    try {
      replaceClaim(await forgetMemoryClaim({ claim, ...DEMO_SCOPE }));
    } catch (requestError) {
      setRequestError(requestError, 'Không thể quên memory.');
    } finally {
      setLoading(false);
    }
  };

  const toggleSources = async (claim: MemoryClaim) => {
    if (claimSources[claim.claim_id]) {
      setClaimSources((current) => {
        const next = { ...current };
        delete next[claim.claim_id];
        return next;
      });
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await getMemoryClaimSources({
        claimId: claim.claim_id,
        ...DEMO_SCOPE
      });
      setClaimSources((current) => ({
        ...current,
        [claim.claim_id]: result.items
      }));
    } catch (requestError) {
      setRequestError(requestError, 'Không thể tải nguồn memory.');
    } finally {
      setLoading(false);
    }
  };

  const runPreview = async () => {
    if (!previewQuery.trim()) return;
    setLoading(true);
    setError(null);
    try {
      setPreview(
        await previewMemoryContext({
          ...DEMO_SCOPE,
          query: previewQuery.trim()
        })
      );
    } catch (requestError) {
      setRequestError(requestError, 'Không thể tạo context preview.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/55"
      role="dialog"
      aria-modal="true"
      aria-label="Memory and context"
    >
      <section className="flex h-full w-full max-w-3xl flex-col border-l border-[var(--color-border)] bg-[var(--color-surface)] shadow-2xl">
        <header className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="rounded-xl bg-[var(--color-accent-soft)] p-2 text-[var(--color-accent)]">
              <BrainCircuit className="h-5 w-5" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-zinc-100">
                Memory & Context Platform
              </h2>
              <p className="text-xs text-zinc-500">
                {DEMO_SCOPE.projectId} · {DEMO_SCOPE.workspaceId}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close memory panel"
            className="rounded-lg p-2 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-2 focus-visible:outline-[var(--color-accent)]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex gap-1 border-b border-[var(--color-border)] px-5 pt-3">
          <button
            type="button"
            onClick={() => {
              setTab('project');
              if (!summary) void loadProjectMemory();
            }}
            className={`flex items-center gap-2 rounded-t-lg px-3 py-2 text-xs font-medium ${
              tab === 'project'
                ? 'bg-zinc-800 text-zinc-100'
                : 'text-zinc-500 hover:text-zinc-200'
            }`}
          >
            <BrainCircuit className="h-3.5 w-3.5" />
            Project Memory
          </button>
          <button
            type="button"
            onClick={() => setTab('memories')}
            className={`flex items-center gap-2 rounded-t-lg px-3 py-2 text-xs font-medium ${
              tab === 'memories'
                ? 'bg-zinc-800 text-zinc-100'
                : 'text-zinc-500 hover:text-zinc-200'
            }`}
          >
            <Database className="h-3.5 w-3.5" />
            Memories
          </button>
          <button
            type="button"
            onClick={() => setTab('snapshot')}
            className={`flex items-center gap-2 rounded-t-lg px-3 py-2 text-xs font-medium ${
              tab === 'snapshot'
                ? 'bg-zinc-800 text-zinc-100'
                : 'text-zinc-500 hover:text-zinc-200'
            }`}
          >
            <ShieldCheck className="h-3.5 w-3.5" />
            Snapshot inspector
          </button>
        </div>

        {error && (
          <div className="mx-5 mt-4 flex items-start gap-2 rounded-xl border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {tab === 'project' ? (
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-5">
            <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-zinc-100">
                      Project Memory Summary
                    </h3>
                    <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[10px] text-zinc-400">
                      {summary?.freshness ?? 'EMPTY'}
                    </span>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-zinc-300">
                    {summary?.content || 'Chưa có bản tóm tắt. Hãy chạy LLM refresh.'}
                  </p>
                  {summary && (
                    <p className="mt-2 text-[10px] text-zinc-600">
                      v{summary.version} · {summary.claim_ids.length} claims ·{' '}
                      {summary.pending_job_count} pending jobs
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => void refreshProjectMemory()}
                  disabled={loading}
                  className="flex shrink-0 items-center gap-2 rounded-xl bg-[var(--color-accent)] px-3 py-2 text-xs font-semibold text-zinc-950 disabled:opacity-50"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
                  LLM refresh
                </button>
              </div>
              {summary?.sections.map((section) => (
                <div
                  key={`${section.title}-${section.claim_ids.join('-')}`}
                  className="mt-3 border-t border-zinc-800 pt-3"
                >
                  <h4 className="text-xs font-medium text-zinc-200">{section.title}</h4>
                  <p className="mt-1 text-xs leading-5 text-zinc-500">{section.content}</p>
                </div>
              ))}
            </div>

            <form
              className="mt-4 rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4"
              onSubmit={(event) => {
                event.preventDefault();
                void runPreview();
              }}
            >
              <label className="text-xs font-medium text-zinc-300">
                Context preview
                <div className="mt-2 flex gap-2">
                  <input
                    value={previewQuery}
                    onChange={(event) => setPreviewQuery(event.target.value)}
                    placeholder="Nhập câu hỏi để xem memory nào sẽ được dùng"
                    className="min-w-0 flex-1 rounded-xl border border-[var(--color-border)] bg-zinc-950 px-3 py-2 text-xs text-zinc-100 outline-none focus:border-[var(--color-accent)]"
                  />
                  <button
                    type="submit"
                    disabled={loading || !previewQuery.trim()}
                    className="rounded-xl border border-[var(--color-border)] px-3 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
                  >
                    Preview
                  </button>
                </div>
              </label>
              {preview && (
                <div className="mt-3 space-y-2 text-xs text-zinc-500">
                  <p>
                    Query: <span className="text-zinc-300">{preview.standalone_query}</span>
                  </p>
                  <p>
                    {preview.items.length} selected · {preview.estimated_token_count}/
                    {preview.token_limit} tokens
                  </p>
                  {preview.items.map((item) => (
                    <div key={item.claim_id} className="rounded-lg bg-zinc-900 p-2">
                      <p className="text-zinc-300">{item.content}</p>
                      <p className="mt-1 text-[10px]">{item.selection_reason}</p>
                    </div>
                  ))}
                </div>
              )}
            </form>

            {(['ACTIVE', 'PROPOSED', 'CONFLICTED'] as const).map((statusName) => {
              const group = claims.filter((claim) => claim.status === statusName);
              return (
                <section key={statusName} className="mt-5">
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-400">
                      {statusName === 'CONFLICTED' ? 'Conflicts' : statusName}
                    </h3>
                    <span className="text-[10px] text-zinc-600">{group.length}</span>
                  </div>
                  <div className="space-y-3">
                    {group.map((claim) => (
                      <article
                        key={claim.claim_id}
                        className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4"
                      >
                        <div className="flex items-start justify-between gap-4">
                          <div className="min-w-0 flex-1">
                            <div className="mb-2 flex flex-wrap gap-2 text-[10px] uppercase tracking-wide text-zinc-500">
                              <span>{claim.category}</span>
                              <span>{claim.sensitivity}</span>
                              <span>v{claim.version}</span>
                              <span>{claim.independent_run_count} runs</span>
                            </div>
                            {editingClaimId === claim.claim_id ? (
                              <div className="space-y-2">
                                <textarea
                                  value={claimDraft}
                                  onChange={(event) => setClaimDraft(event.target.value)}
                                  aria-label={`Edit claim ${claim.claim_id}`}
                                  className="min-h-20 w-full rounded-xl border border-[var(--color-border)] bg-zinc-950 p-3 text-xs text-zinc-100 outline-none focus:border-[var(--color-accent)]"
                                />
                                <div className="flex gap-2">
                                  <button
                                    type="button"
                                    onClick={() => void saveClaimCorrection(claim)}
                                    disabled={loading || !claimDraft.trim()}
                                    className="rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-semibold text-zinc-950 disabled:opacity-50"
                                  >
                                    Save correction
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => setEditingClaimId(null)}
                                    className="rounded-lg px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800"
                                  >
                                    Cancel
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <p className="text-sm leading-6 text-zinc-200">{claim.content}</p>
                            )}
                            {claim.decision_reason && (
                              <p className="mt-2 text-[10px] text-zinc-600">
                                {claim.decision_reason}
                              </p>
                            )}
                          </div>
                          <div className="flex shrink-0 flex-wrap justify-end gap-1">
                            {claim.status !== 'ACTIVE' && (
                              <button
                                type="button"
                                onClick={() => void confirmClaim(claim)}
                                disabled={loading}
                                className="rounded-lg px-2 py-1.5 text-xs text-emerald-400 hover:bg-emerald-950/30 disabled:opacity-50"
                              >
                                Confirm
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={() => {
                                setEditingClaimId(claim.claim_id);
                                setClaimDraft(claim.content);
                              }}
                              className="rounded-lg px-2 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800"
                            >
                              Correct
                            </button>
                            <button
                              type="button"
                              onClick={() => void forgetClaim(claim)}
                              disabled={loading}
                              className="rounded-lg px-2 py-1.5 text-xs text-red-400 hover:bg-red-950/30 disabled:opacity-50"
                            >
                              Forget
                            </button>
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => void toggleSources(claim)}
                          className="mt-3 text-[10px] text-zinc-500 hover:text-zinc-300"
                        >
                          {claimSources[claim.claim_id] ? 'Hide' : 'Show'} sources (
                          {claim.evidence_count})
                        </button>
                        {claimSources[claim.claim_id] && (
                          <div className="mt-2 space-y-1 rounded-xl bg-zinc-950 p-3 text-[10px] text-zinc-500">
                            {claimSources[claim.claim_id].map((source) => (
                              <p key={source.evidence_id}>
                                {source.stance} · {source.source_kind} ·{' '}
                                {source.source_run_id ?? source.source_id}
                              </p>
                            ))}
                          </div>
                        )}
                      </article>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        ) : tab === 'memories' ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <form
              className="flex gap-2 px-5 py-4"
              onSubmit={(event) => {
                event.preventDefault();
                void load();
              }}
            >
              <label className="relative flex-1">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-600" />
                <span className="sr-only">Search memories</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search active and historical memory"
                  className="w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-canvas)] py-2 pl-9 pr-3 text-xs text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-[var(--color-accent)]"
                />
              </label>
              <button
                type="submit"
                disabled={loading}
                className="rounded-xl border border-[var(--color-border)] px-3 text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
                aria-label="Refresh memories"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              </button>
            </form>

            <div className="flex-1 space-y-3 overflow-y-auto px-5 pb-5">
              {!loading && items.length === 0 && !error && (
                <div className="rounded-2xl border border-dashed border-[var(--color-border)] p-8 text-center">
                  <Database className="mx-auto mb-3 h-6 w-6 text-zinc-600" />
                  <p className="text-sm text-zinc-300">Chưa có memory trong scope.</p>
                  <p className="mt-1 text-xs text-zinc-600">
                    Module 1 cần promote một candidate đã xác minh.
                  </p>
                </div>
              )}
              {items.map((memory) => (
                <article
                  key={memory.memory_id}
                  className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="mb-2 flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wide">
                        <span className="rounded-full bg-zinc-800 px-2 py-1 text-zinc-300">
                          {memory.memory_type}
                        </span>
                        <span
                          className={
                            memory.status === 'ACTIVE'
                              ? 'text-emerald-400'
                              : 'text-amber-400'
                          }
                        >
                          {memory.status}
                        </span>
                        <span className="text-zinc-600">v{memory.version}</span>
                      </div>
                      {editingId === memory.memory_id ? (
                        <div className="space-y-2">
                          <textarea
                            value={draft}
                            onChange={(event) => setDraft(event.target.value)}
                            className="min-h-24 w-full rounded-xl border border-[var(--color-border)] bg-zinc-950 p-3 text-xs text-zinc-100 outline-none focus:border-[var(--color-accent)]"
                            aria-label={`Edit memory ${memory.memory_id}`}
                          />
                          <div className="flex gap-2">
                            <button
                              type="button"
                              onClick={() => void saveEdit(memory)}
                              disabled={loading || !draft.trim()}
                              className="rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-semibold text-zinc-950 disabled:opacity-50"
                            >
                              Save new version
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditingId(null)}
                              className="rounded-lg px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <p className="whitespace-pre-wrap text-sm leading-6 text-zinc-200">
                          {memory.content ?? 'Immutable content reference'}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          setEditingId(memory.memory_id);
                          setDraft(memory.content ?? '');
                        }}
                        disabled={memory.status === 'DELETED'}
                        className="rounded-lg px-2 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 disabled:opacity-30"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeletingId(memory.memory_id)}
                        disabled={memory.status === 'DELETED'}
                        aria-label={`Delete memory ${memory.memory_id}`}
                        className="rounded-lg p-1.5 text-zinc-500 hover:bg-red-950/40 hover:text-red-300 disabled:opacity-30"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-zinc-800 pt-3 text-[10px] text-zinc-600">
                    <span>{memory.scope_type}: {memory.scope_id}</span>
                    <span>{memory.source_refs.length} source refs</span>
                    <span>{memory.authority}</span>
                    {memory.conflict_group_id && (
                      <span>Conflict: {memory.conflict_group_id}</span>
                    )}
                  </div>
                  {deletingId === memory.memory_id && (
                    <div className="mt-3 rounded-xl border border-red-900/40 bg-red-950/20 p-3 text-xs text-red-100">
                      <p>
                        Preview: memory này sẽ chuyển sang DELETED; snapshot cũ vẫn bất biến.
                      </p>
                      <div className="mt-2 flex gap-2">
                        <button
                          type="button"
                          onClick={() => void confirmDelete(memory)}
                          disabled={loading}
                          className="rounded-lg bg-red-400 px-3 py-1.5 font-semibold text-red-950 disabled:opacity-50"
                        >
                          Confirm invalidate
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeletingId(null)}
                          className="rounded-lg px-3 py-1.5 text-zinc-400 hover:bg-zinc-800"
                        >
                          Keep memory
                        </button>
                      </div>
                    </div>
                  )}
                </article>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto p-5">
            <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4">
              <div className="mb-4 flex items-start gap-3">
                <ShieldCheck className="mt-0.5 h-5 w-5 text-[var(--color-accent)]" />
                <div>
                  <h3 className="text-sm font-semibold text-zinc-100">
                    Exact-read inspector
                  </h3>
                  <p className="mt-1 text-xs leading-5 text-zinc-500">
                    Token phải khớp snapshot, module, job, attempt và operation revision.
                    Token không được lưu sau khi resolve.
                  </p>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs text-zinc-400">
                  Snapshot ID
                  <input
                    value={snapshotId}
                    onChange={(event) => setSnapshotId(event.target.value)}
                    className="mt-1 w-full rounded-xl border border-[var(--color-border)] bg-zinc-950 p-2.5 text-zinc-100 outline-none focus:border-[var(--color-accent)]"
                  />
                </label>
                <label className="text-xs text-zinc-400">
                  Job ID
                  <input
                    value={jobId}
                    onChange={(event) => setJobId(event.target.value)}
                    className="mt-1 w-full rounded-xl border border-[var(--color-border)] bg-zinc-950 p-2.5 text-zinc-100 outline-none focus:border-[var(--color-accent)]"
                  />
                </label>
                <label className="text-xs text-zinc-400 sm:col-span-2">
                  Context access token
                  <input
                    type="password"
                    autoComplete="off"
                    value={accessToken}
                    onChange={(event) => setAccessToken(event.target.value)}
                    className="mt-1 w-full rounded-xl border border-[var(--color-border)] bg-zinc-950 p-2.5 font-mono text-zinc-100 outline-none focus:border-[var(--color-accent)]"
                  />
                </label>
              </div>
              <button
                type="button"
                onClick={() => void inspectSnapshot()}
                disabled={
                  loading || !snapshotId.trim() || !jobId.trim() || !accessToken
                }
                className="mt-4 rounded-xl bg-[var(--color-accent)] px-4 py-2 text-xs font-semibold text-zinc-950 disabled:opacity-50"
              >
                Resolve exact snapshot
              </button>
            </div>

            {snapshot && (
              <div className="mt-4 space-y-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4 text-xs">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-zinc-300">
                    {snapshot.context_snapshot_id}
                  </span>
                  <span className="text-zinc-500">v{snapshot.context_version}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-zinc-800">
                  <div
                    className="h-full bg-[var(--color-accent)]"
                    style={{
                      width: `${Math.min(
                        100,
                        (snapshot.estimated_token_count / snapshot.token_limit) * 100
                      )}%`
                    }}
                  />
                </div>
                <p className="text-zinc-500">
                  {snapshot.estimated_token_count} / {snapshot.token_limit} tokens ·{' '}
                  {snapshot.selected_memory_items.length} selected items
                </p>
                <dl className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <dt className="text-zinc-600">Policy</dt>
                    <dd className="text-zinc-300">{snapshot.policy_version}</dd>
                  </div>
                  <div>
                    <dt className="text-zinc-600">Content hash</dt>
                    <dd className="truncate font-mono text-zinc-300">
                      {snapshot.content_hash}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-zinc-600">Conflicts</dt>
                    <dd className="text-zinc-300">
                      {snapshot.conflict_groups.length}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-zinc-600">Truncated</dt>
                    <dd className="text-zinc-300">
                      {snapshot.truncated ? 'Yes' : 'No'}
                    </dd>
                  </div>
                </dl>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
