import { useEffect, useState, useMemo } from 'react';
import {
  X,
  Files,
  Check,
  Copy,
  LoaderCircle,
  Circle,
  AlertCircle,
  Layers,
  BrainCircuit,
  Database,
  ShieldCheck,
  Search,
  ChevronDown,
  ChevronUp,
  Bookmark,
  FileText,
} from 'lucide-react';
import type {
  ChatActivity,
  ChatActivityCode,
  ChatActivityStatus,
  ChatExecutionTrace,
  ChatGenerationStatus,
  ChatMessage,
} from '../types';
import { formatSecondsVi, formatShortSecondsVi, spanMilliseconds } from './reasoningDuration';

export interface ExecutionTraceDrawerProps {
  trace?: ChatExecutionTrace;
  activities?: ChatActivity[];
  generationStatus?: ChatGenerationStatus;
  message?: ChatMessage;
  activeProjectName?: string;
  sessionTurnCount?: number;
  onClose: () => void;
}

type DrawerTab = 'process' | 'memory';

const TABS: Array<{ id: DrawerTab; icon: string; label: string }> = [
  { id: 'process', icon: '✦', label: 'Tiến trình xử lý' },
  { id: 'memory', icon: '🧠', label: 'Bộ nhớ' },
];

function findActivity(
  activities: ChatActivity[] | undefined,
  ...codes: ChatActivityCode[]
): ChatActivity | undefined {
  return activities?.find((activity) => codes.includes(activity.code));
}

function stepStatus(activity: ChatActivity | undefined, trace?: ChatExecutionTrace): ChatActivityStatus {
  if (activity) return activity.status;
  return trace ? 'completed' : 'pending';
}

function StatusNode({ status }: { status: ChatActivityStatus }) {
  return (
    <span className="relative z-10 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-[#413b34] bg-[#181715]">
      {status === 'completed' ? (
        <Check className="h-3.5 w-3.5 text-emerald-400" />
      ) : status === 'running' ? (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin text-[#e8a78f] motion-reduce:animate-none" />
      ) : status === 'failed' || status === 'cancelled' ? (
        <AlertCircle className="h-3.5 w-3.5 text-rose-400" />
      ) : (
        <Circle className="h-3 w-3 text-zinc-600" />
      )}
    </span>
  );
}

export function ExecutionTraceDrawer({
  trace,
  activities,
  generationStatus,
  message,
  activeProjectName,
  sessionTurnCount,
  onClose,
}: ExecutionTraceDrawerProps) {
  const isGenerating = generationStatus === 'generating';
  const [activeTab, setActiveTab] = useState<DrawerTab>('process');
  const [copied, setCopied] = useState(false);
  const [copiedMemory, setCopiedMemory] = useState(false);
  const [evidenceSearch, setEvidenceSearch] = useState('');
  const [expandedChunkIds, setExpandedChunkIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  useEffect(() => {
    if (!copiedMemory) return;
    const timer = window.setTimeout(() => setCopiedMemory(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copiedMemory]);

  const understandingActivity = findActivity(activities, 'understanding_request');
  const searchActivity = findActivity(activities, 'searching_relevant_information');
  const contextActivity = findActivity(activities, 'reviewing_context');
  const modelActivity = findActivity(activities, 'preparing_response', 'preparing_action_plan');

  const filenames = trace?.retrievedFilenames ?? [];
  const ragEvidence = useMemo(() => message?.ragEvidence ?? [], [message?.ragEvidence]);
  const isRagRoute = filenames.length > 0 || ragEvidence.length > 0 || Boolean(searchActivity);
  const chunkCount =
    searchActivity?.detail?.kind === 'documents_found'
      ? searchActivity.detail.current
      : ragEvidence.length > 0
        ? ragEvidence.length
        : filenames.length;
  const understandingMs = spanMilliseconds(understandingActivity?.startedAt, understandingActivity?.completedAt);
  const searchMs = spanMilliseconds(searchActivity?.startedAt, searchActivity?.completedAt);
  const contextMs = spanMilliseconds(contextActivity?.startedAt, contextActivity?.completedAt);
  const retrievalMs = searchMs ?? contextMs;
  const reasoningMs = spanMilliseconds(modelActivity?.startedAt, modelActivity?.completedAt);
  const isMemoryDegraded = contextActivity?.outcome === 'degraded';

  const totalExecutionMs = useMemo(() => {
    const first = activities?.find((a) => a.startedAt)?.startedAt;
    const last = activities?.findLast((a) => a.completedAt)?.completedAt;
    return spanMilliseconds(first, last);
  }, [activities]);

  const handleCopyReasoning = () => {
    if (!trace?.reasoning) return;
    void navigator.clipboard?.writeText(trace.reasoning);
    setCopied(true);
  };

  const handleCopyMemoryContext = () => {
    const memorySnapshot = {
      project: activeProjectName || 'Default Project',
      turn_id: message?.turnId || 'current-turn',
      session_turn_count: sessionTurnCount || 1,
      memory_tiers: {
        short_term: {
          status: 'active',
          description: 'Session Buffer in RAM (20 turns / 30m window)',
          turns_in_buffer: sessionTurnCount || 1,
        },
        long_term: {
          status: 'synced',
          description: 'User Profile & Project Scope',
          project_context: activeProjectName || 'Default Project',
          language: 'vi-VN',
        },
        episodic: {
          status: isMemoryDegraded ? 'degraded' : 'synced',
          task_id: message?.taskId || null,
          task_status: message?.taskStatus || null,
        },
        semantic: {
          status: isRagRoute ? 'retrieved' : 'skipped_deterministic_gate',
          retrieved_chunks: ragEvidence.map((e) => ({
            chunk_id: e.chunkId,
            document: e.documentTitle,
            section: e.section,
            relevance: e.relevanceScore,
            source: e.source,
          })),
          retrieved_filenames: filenames,
        },
      },
      isolation_guard: {
        tenant: 'local',
        feature: 'ai_chat',
        fail_closed: true,
      },
    };

    void navigator.clipboard?.writeText(JSON.stringify(memorySnapshot, null, 2));
    setCopiedMemory(true);
  };

  const toggleChunkExpansion = (chunkId: string) => {
    setExpandedChunkIds((prev) => {
      const next = new Set(prev);
      if (next.has(chunkId)) {
        next.delete(chunkId);
      } else {
        next.add(chunkId);
      }
      return next;
    });
  };

  const filteredEvidence = useMemo(() => {
    if (!evidenceSearch.trim()) return ragEvidence;
    const q = evidenceSearch.toLowerCase();
    return ragEvidence.filter(
      (item) =>
        item.documentTitle.toLowerCase().includes(q) ||
        (item.section && item.section.toLowerCase().includes(q)) ||
        item.preview.toLowerCase().includes(q) ||
        item.content.toLowerCase().includes(q)
    );
  }, [ragEvidence, evidenceSearch]);

  return (
    <aside
      aria-label="Chi tiết xử lý của mô hình"
      className="flex min-h-0 flex-col border-l border-[#413b34] bg-[#201e1b]"
    >
      {/* Drawer Header */}
      <header className="flex items-start justify-between gap-4 border-b border-[#413b34] px-5 py-4">
        <div>
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-[#e8a78f]" />
            <p className="text-sm font-semibold text-zinc-100">Chi tiết xử lý</p>
          </div>
          {trace ? (
            <p className="mt-1 text-xs text-zinc-400">
              <span className="font-medium text-zinc-300">{trace.provider}</span> · {trace.model} ·{' '}
              <span className={trace.mode === 'fast' ? 'text-amber-400/90' : 'text-emerald-400/90'}>
                {trace.mode === 'fast' ? 'Nhanh' : 'Suy luận'}
              </span>
              {totalExecutionMs !== null && (
                <span className="text-zinc-500"> · Tổng {formatSecondsVi(totalExecutionMs)}s</span>
              )}
            </p>
          ) : isGenerating ? (
            <p className="mt-1 text-xs text-[#e8a78f] animate-pulse">Đang thu thập thông tin xử lý...</p>
          ) : (
            <p className="mt-1 text-xs text-zinc-400">Đang chờ mô hình trả chi tiết.</p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
          aria-label="Đóng chi tiết xử lý"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      {/* Segmented slider control */}
      <div className="px-5 pt-4">
        <div
          role="tablist"
          aria-label="Chế độ xem chi tiết xử lý"
          className="relative flex rounded-lg border border-[#413b34] bg-[#181715] p-1"
        >
          <span
            aria-hidden="true"
            className={`absolute inset-y-1 left-1 w-[calc(50%-0.25rem)] rounded-md bg-[#33302a] transition-transform duration-200 motion-reduce:transition-none ${
              activeTab === 'memory' ? 'translate-x-full' : 'translate-x-0'
            }`}
          />
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`trace-tab-${tab.id}`}
              aria-selected={activeTab === tab.id}
              aria-controls={`trace-panel-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`relative z-10 flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                activeTab === tab.id ? 'text-zinc-100' : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <span aria-hidden="true">{tab.icon}</span> {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Drawer Content */}
      <div className="min-h-0 overflow-y-auto p-5 custom-scrollbar">
        {activeTab === 'process' ? (
          <div role="tabpanel" id="trace-panel-process" aria-labelledby="trace-tab-process">
            <ol aria-label="Tiến trình xử lý" className="relative space-y-6">
              {/* Continuous connector line behind the status nodes */}
              <span aria-hidden="true" className="absolute left-3 top-3 bottom-3 w-px bg-[#413b34]" />

              {/* Step 1 — Hiểu yêu cầu */}
              <li className="relative flex gap-3">
                <StatusNode status={stepStatus(understandingActivity, trace)} />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-zinc-200">1. Hiểu yêu cầu</p>
                    {understandingMs !== null && (
                      <span className="font-mono text-[11px] text-zinc-400 bg-[#181715] border border-[#38342e] px-1.5 py-0.5 rounded">
                        {formatShortSecondsVi(understandingMs)}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
                    <span>Định tuyến:</span>
                    <span
                      className={`rounded border px-2 py-0.5 text-[11px] font-medium ${
                        isRagRoute
                          ? 'border-[#6d3e2e] bg-[#38231a] text-[#e8a78f]'
                          : 'border-[#413b34] bg-[#24211d] text-zinc-300'
                      }`}
                    >
                      {isRagRoute ? 'RAG · Truy xuất tài liệu' : 'Direct · Hội thoại trực tiếp'}
                    </span>
                    {understandingMs !== null && (
                      <span className="text-zinc-500">· {formatSecondsVi(understandingMs)} giây</span>
                    )}
                  </div>
                </div>
              </li>

              {/* Step 2 — Tìm thông tin liên quan */}
              <li className="relative flex gap-3">
                <StatusNode status={stepStatus(searchActivity, trace)} />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-zinc-200">2. Tìm thông tin liên quan</p>
                    {retrievalMs !== null && isRagRoute && (
                      <span className="font-mono text-[11px] text-zinc-400 bg-[#181715] border border-[#38342e] px-1.5 py-0.5 rounded">
                        {formatShortSecondsVi(retrievalMs)}
                      </span>
                    )}
                  </div>
                  {isRagRoute ? (
                    <>
                      <p className="text-xs text-zinc-400">
                        Đã tìm thấy{' '}
                        <span className="font-medium text-zinc-200">{chunkCount}</span> đoạn nội dung liên quan
                        {retrievalMs !== null && (
                          <span> · Truy vấn trong {formatSecondsVi(retrievalMs)} giây</span>
                        )}
                      </p>
                      {filenames.length > 0 && (
                        <ul className="flex flex-wrap gap-1.5">
                          {filenames.map((filename) => (
                            <li
                              key={filename}
                              className="inline-flex items-center gap-1 rounded border border-[#413b34] bg-[#181715] px-2 py-0.5 font-mono text-xs text-zinc-300"
                            >
                              <Files className="h-3 w-3 shrink-0 text-[#e8a78f]" aria-hidden="true" />
                              <span className="truncate max-w-[220px]">{filename}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  ) : (
                    <p className="text-xs text-zinc-400">Không yêu cầu truy xuất tài liệu</p>
                  )}
                </div>
              </li>

              {/* Step 3 — Tổng hợp câu trả lời */}
              <li className="relative flex gap-3">
                <StatusNode status={stepStatus(modelActivity, trace)} />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-zinc-200">3. Tổng hợp câu trả lời</p>
                    {reasoningMs !== null && (
                      <span className="font-mono text-[11px] text-zinc-400 bg-[#181715] border border-[#38342e] px-1.5 py-0.5 rounded">
                        {formatShortSecondsVi(reasoningMs)}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    {trace && (
                      <span className="rounded border border-[#6d3e2e] bg-[#38231a] px-2 py-0.5 font-medium text-[#e8a78f]">
                        {trace.model}
                      </span>
                    )}
                    {reasoningMs !== null && (
                      <span className="text-zinc-400">
                        {trace?.mode === 'reasoning' ? 'Suy luận trong' : 'Tổng hợp trong'} {formatSecondsVi(reasoningMs)} giây
                      </span>
                    )}
                  </div>

                  {trace?.reasoning ? (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[11px] font-semibold text-zinc-300">
                          Chuỗi suy luận (Chain of Thought):
                        </span>
                        <div className="flex items-center gap-2">
                          {trace.reasoningTruncated && (
                            <span className="text-[10px] text-amber-300">Đã rút gọn</span>
                          )}
                          <button
                            type="button"
                            onClick={handleCopyReasoning}
                            aria-label="Sao chép chuỗi suy luận"
                            className="inline-flex items-center gap-1 rounded border border-[#413b34] bg-[#181715] px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:text-zinc-100 cursor-pointer"
                          >
                            {copied ? (
                              <>
                                <Check className="h-3 w-3 text-emerald-400" aria-hidden="true" /> Đã sao chép!
                              </>
                            ) : (
                              <>
                                <Copy className="h-3 w-3" aria-hidden="true" /> Sao chép
                              </>
                            )}
                          </button>
                        </div>
                      </div>
                      <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-[#413b34] bg-[#121110] p-3 font-mono text-xs leading-5 text-zinc-300">
                        {trace.reasoning}
                      </pre>
                      {trace.reasoningTruncated && (
                        <p className="text-xs text-amber-300">Reasoning đã được rút gọn để lưu an toàn.</p>
                      )}
                    </div>
                  ) : trace?.mode === 'fast' ? (
                    <div className="rounded border border-[#413b34] bg-[#141312] p-2.5 text-xs text-zinc-400">
                      <span className="font-semibold text-zinc-300">Chế độ Nhanh:</span> Mô hình sinh câu trả lời
                      trực tiếp không qua bước suy luận sâu (Thinking disabled).
                    </div>
                  ) : isGenerating ? (
                    <div className="rounded border border-[#52382c] bg-[#1e1713] p-2.5 text-xs text-[#e8a78f] animate-pulse">
                      Đang chờ mô hình thực hiện suy luận...
                    </div>
                  ) : (
                    <p className="text-xs text-zinc-500">Nhà cung cấp không trả reasoning cho lượt này.</p>
                  )}
                </div>
              </li>
            </ol>
          </div>
        ) : (
          <section
            role="tabpanel"
            id="trace-panel-memory"
            aria-labelledby="trace-tab-memory"
            className="space-y-4"
          >
            {/* Header with status title */}
            <div className="flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">
                <BrainCircuit className="h-4 w-4 text-[#e8a78f]" /> Trạng thái bộ nhớ
              </h2>
              <button
                type="button"
                onClick={handleCopyMemoryContext}
                aria-label="Sao chép bối cảnh bộ nhớ"
                className="inline-flex items-center gap-1 rounded border border-[#413b34] bg-[#181715] px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:text-zinc-100 hover:border-[#d97757]/40 cursor-pointer"
                title="Sao chép toàn bộ snapshot bộ nhớ của lượt hội thoại"
              >
                {copiedMemory ? (
                  <>
                    <Check className="h-3 w-3 text-emerald-400" aria-hidden="true" /> Đã sao chép!
                  </>
                ) : (
                  <>
                    <Copy className="h-3 w-3 text-[#e8a78f]" aria-hidden="true" /> Sao chép bối cảnh
                  </>
                )}
              </button>
            </div>

            {/* Existing dl structure for 100% test backward-compatibility */}
            {contextActivity ? (
              <dl className="space-y-2 rounded-lg border border-[#413b34] bg-[#181715] p-3.5 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-zinc-400">Bộ nhớ tình tiết (Episodic):</dt>
                  <dd className={isMemoryDegraded ? 'font-medium text-amber-400' : 'font-medium text-emerald-400/90'}>
                    {isMemoryDegraded ? 'Một phần suy giảm' : 'Sẵn sàng & Đồng bộ'}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <dt className="text-zinc-400">Bộ nhớ làm việc (Working):</dt>
                  <dd className={isMemoryDegraded ? 'font-medium text-amber-400' : 'font-medium text-emerald-400/90'}>
                    {isMemoryDegraded ? 'Một phần suy giảm' : 'Sẵn sàng & Đồng bộ'}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="text-xs text-zinc-500">Lượt này không sử dụng bộ nhớ ngữ cảnh.</p>
            )}

            <p className="text-xs leading-relaxed text-zinc-500">
              Bộ nhớ ngữ cảnh đối chiếu lịch sử hội thoại và hồ sơ dự án trước khi mô hình sinh câu trả lời.
            </p>

            {/* 4-Tier Memory Architecture Breakdown */}
            <div className="pt-2 space-y-3">
              <div className="flex items-center justify-between border-t border-[#33302a] pt-3">
                <h3 className="text-xs font-semibold text-zinc-300 flex items-center gap-1.5">
                  <Database className="h-3.5 w-3.5 text-[#e8a78f]" />
                  <span>4 Tầng Bộ Nhớ AI Đang Sử Dụng</span>
                </h3>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#2c2824] text-zinc-400 border border-[#3e3a34]">
                  Typed Architecture
                </span>
              </div>

              {/* TIER 1: SHORT-TERM (SESSION BUFFER) */}
              <div className="rounded-xl border border-[#38342e] bg-[#1a1917] p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="flex h-5 w-5 items-center justify-center rounded-md bg-amber-500/10 text-amber-400 text-[10px] font-bold">
                      T1
                    </span>
                    <div>
                      <h4 className="text-xs font-medium text-zinc-200">Short-Term (Session Buffer)</h4>
                      <p className="text-[10px] text-zinc-500">Bộ nhớ đệm hội thoại trong RAM</p>
                    </div>
                  </div>
                  <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-emerald-950/40 border border-emerald-800/40 text-emerald-400">
                    Đang hoạt động
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 pt-1 text-[11px] text-zinc-400">
                  <div className="rounded-lg bg-[#22201d] p-2 border border-[#2d2b27]">
                    <span className="block text-[10px] text-zinc-500">Vòng đời ngữ cảnh</span>
                    <span className="font-mono text-zinc-300 text-xs">20 lượt / 30m idle</span>
                  </div>
                  <div className="rounded-lg bg-[#22201d] p-2 border border-[#2d2b27]">
                    <span className="block text-[10px] text-zinc-500">Lượt hội thoại phiên</span>
                    <span className="font-mono text-zinc-300 text-xs">{sessionTurnCount ?? 1} lượt</span>
                  </div>
                </div>
              </div>

              {/* TIER 2: LONG-TERM (DECLARATIVE PROFILE) */}
              <div className="rounded-xl border border-[#38342e] bg-[#1a1917] p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="flex h-5 w-5 items-center justify-center rounded-md bg-blue-500/10 text-blue-400 text-[10px] font-bold">
                      T2
                    </span>
                    <div>
                      <h4 className="text-xs font-medium text-zinc-200">Long-Term (Hồ sơ & Sở thích)</h4>
                      <p className="text-[10px] text-zinc-500">Người dùng cấu hình tường minh</p>
                    </div>
                  </div>
                  <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-blue-950/40 border border-blue-800/40 text-blue-400">
                    Đã đồng bộ
                  </span>
                </div>
                <div className="rounded-lg bg-[#22201d] p-2 border border-[#2d2b27] text-[11px] text-zinc-400 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-zinc-500">Không gian dự án:</span>
                    <span className="font-medium text-zinc-300">{activeProjectName || 'General / Mặc định'}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-zinc-500">Ngôn ngữ ưu tiên:</span>
                    <span className="text-zinc-300">Tiếng Việt (vi-VN)</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-zinc-500">Quy tắc Persona:</span>
                    <span className="text-zinc-300">Đính kèm trích dẫn nguồn RAG</span>
                  </div>
                </div>
              </div>

              {/* TIER 3: EPISODIC MEMORY (TASK EPISODES) */}
              <div className="rounded-xl border border-[#38342e] bg-[#1a1917] p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="flex h-5 w-5 items-center justify-center rounded-md bg-purple-500/10 text-purple-400 text-[10px] font-bold">
                      T3
                    </span>
                    <div>
                      <h4 className="text-xs font-medium text-zinc-200">Episodic (Ký ức tình tiết & Tác vụ)</h4>
                      <p className="text-[10px] text-zinc-500">Đề xuất bởi AI · Phê duyệt bởi User</p>
                    </div>
                  </div>
                  <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-purple-950/40 border border-purple-800/40 text-purple-400">
                    {message?.taskId ? 'Có tác vụ liên kết' : 'Sẵn sàng'}
                  </span>
                </div>
                <div className="rounded-lg bg-[#22201d] p-2 border border-[#2d2b27] text-[11px] text-zinc-400 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-zinc-500">Mã tác vụ (Task ID):</span>
                    <span className="font-mono text-zinc-300">{message?.taskId || 'Không có'}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-zinc-500">Trạng thái phê duyệt:</span>
                    <span className="text-zinc-300">{message?.taskStatus || 'Hội thoại trực tiếp'}</span>
                  </div>
                  <div className="text-[10px] text-zinc-500 pt-0.5">
                    Chỉ những kế hoạch được phê duyệt mới được lưu vào Postgres task_episodes để đối chiếu lại.
                  </div>
                </div>
              </div>

              {/* TIER 4: SEMANTIC MEMORY (ENTERPRISE RAG & KNOWLEDGE) */}
              <div className="rounded-xl border border-[#38342e] bg-[#1a1917] p-3 space-y-2.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="flex h-5 w-5 items-center justify-center rounded-md bg-[#d97757]/20 text-[#e8a78f] text-[10px] font-bold">
                      T4
                    </span>
                    <div>
                      <h4 className="text-xs font-medium text-zinc-200">Semantic (Tri thức & RAG)</h4>
                      <p className="text-[10px] text-zinc-500">Turbovec Hybrid Retrieval (Chỉ đọc)</p>
                    </div>
                  </div>
                  <span
                    className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${
                      isRagRoute
                        ? 'bg-[#38231a] border-[#6d3e2e] text-[#e8a78f]'
                        : 'bg-zinc-800/40 border-zinc-700/40 text-zinc-400'
                    }`}
                  >
                    {isRagRoute ? `Truy xuất ${chunkCount} đoạn` : 'Bỏ qua (Direct Turn)'}
                  </span>
                </div>

                {/* Evidence List & Details */}
                {isRagRoute ? (
                  <div className="space-y-2 pt-1">
                    {ragEvidence.length > 0 && (
                      <div className="relative">
                        <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-zinc-500" />
                        <input
                          type="text"
                          placeholder="Lọc đoạn trích dẫn tri thức..."
                          value={evidenceSearch}
                          onChange={(e) => setEvidenceSearch(e.target.value)}
                          className="w-full pl-8 pr-3 py-1.5 text-[11px] rounded-lg bg-[#22201d] border border-[#33302a] text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-[#d97757]/50"
                        />
                      </div>
                    )}

                    {filteredEvidence.length > 0 ? (
                      <div className="space-y-2 max-h-80 overflow-y-auto pr-0.5 custom-scrollbar">
                        {filteredEvidence.map((item, idx) => {
                          const isExpanded = expandedChunkIds.has(item.chunkId);
                          return (
                            <div
                              key={item.chunkId || idx}
                              className="rounded-lg border border-[#3e3933] bg-[#22201d] p-2.5 text-xs space-y-1.5 transition-all"
                            >
                              <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-1.5 text-zinc-200 font-medium">
                                    <FileText className="h-3.5 w-3.5 text-[#e8a78f] shrink-0" />
                                    <span className="truncate">{item.documentTitle}</span>
                                  </div>
                                  {item.section && (
                                    <span className="inline-block mt-0.5 text-[10px] text-zinc-400 bg-[#2e2a25] px-1.5 py-0.5 rounded border border-[#454038]">
                                      {item.section}
                                    </span>
                                  )}
                                </div>
                                <div className="text-right shrink-0">
                                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-950/40 text-emerald-400 border border-emerald-800/40">
                                    {(item.relevanceScore * 100).toFixed(1)}% khớp
                                  </span>
                                  {item.rerankScore !== null && (
                                    <span className="block text-[9px] text-zinc-500 font-mono mt-0.5">
                                      Rerank: {(item.rerankScore * 100).toFixed(0)}%
                                    </span>
                                  )}
                                </div>
                              </div>

                              {/* Content preview or full text */}
                              <div className="rounded bg-[#171614] p-2 font-mono text-[10px] leading-relaxed text-zinc-300 border border-[#2d2b27] whitespace-pre-wrap break-words">
                                {isExpanded && item.content ? item.content : item.preview}
                              </div>

                              <div className="flex items-center justify-between pt-0.5 text-[10px] text-zinc-500">
                                <span>Nguồn: {item.source === 'company_knowledge' ? 'Tri thức công ty' : 'Tài liệu dự án'}</span>
                                <button
                                  type="button"
                                  onClick={() => toggleChunkExpansion(item.chunkId)}
                                  className="inline-flex items-center gap-0.5 text-[#e8a78f] hover:underline cursor-pointer"
                                >
                                  {isExpanded ? (
                                    <>
                                      <span>Thu gọn</span>
                                      <ChevronUp className="h-3 w-3" />
                                    </>
                                  ) : (
                                    <>
                                      <span>Xem đầy đủ</span>
                                      <ChevronDown className="h-3 w-3" />
                                    </>
                                  )}
                                </button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : filenames.length > 0 ? (
                      <div className="rounded-lg bg-[#22201d] p-2 border border-[#2d2b27] space-y-1">
                        <span className="text-[10px] text-zinc-500 block">Tài liệu tham chiếu:</span>
                        <div className="flex flex-wrap gap-1">
                          {filenames.map((name) => (
                            <span
                              key={name}
                              className="inline-flex items-center gap-1 rounded bg-[#171614] border border-[#3e3933] px-2 py-0.5 font-mono text-[10px] text-zinc-300"
                            >
                              <Bookmark className="h-2.5 w-2.5 text-[#e8a78f]" />
                              {name}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <p className="text-[11px] text-zinc-500 italic">Không có bằng chứng RAG chi tiết cho lượt này.</p>
                    )}
                  </div>
                ) : (
                  <div className="rounded-lg bg-[#22201d] p-2.5 border border-[#2d2b27] text-[11px] text-zinc-400 space-y-1">
                    <p>
                      Cổng truy xuất xác định câu hỏi không cần tra cứu chính sách/tài liệu để tránh sinh ảo tưởng (Anti-hallucination).
                    </p>
                  </div>
                )}
              </div>

              {/* Isolation & Anti-Leak Guard Banner */}
              <div className="rounded-xl border border-emerald-900/30 bg-emerald-950/15 p-3 flex items-start gap-2.5 text-xs">
                <ShieldCheck className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                <div className="space-y-0.5">
                  <p className="font-medium text-emerald-300">Cơ chế cách ly bộ nhớ (Anti-Leak Guard)</p>
                  <p className="text-[10px] text-emerald-500/90 leading-relaxed">
                    Mọi truy vấn bộ nhớ đều được gắn nhãn định danh Tenant, User, Session và Project ID trước khi tìm kiếm. Cơ chế Fail-Closed đảm bảo tuyệt đối không rò rỉ dữ liệu chéo giữa các người dùng.
                  </p>
                </div>
              </div>
            </div>
          </section>
        )}
      </div>
    </aside>
  );
}
