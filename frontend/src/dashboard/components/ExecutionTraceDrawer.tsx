import { useEffect, useState } from 'react';
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
} from 'lucide-react';
import type {
  ChatActivity,
  ChatActivityCode,
  ChatActivityStatus,
  ChatExecutionTrace,
  ChatGenerationStatus,
} from '../types';
import { formatSecondsVi, spanMilliseconds } from './reasoningDuration';

export interface ExecutionTraceDrawerProps {
  trace?: ChatExecutionTrace;
  activities?: ChatActivity[];
  generationStatus?: ChatGenerationStatus;
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
  onClose,
}: ExecutionTraceDrawerProps) {
  const isGenerating = generationStatus === 'generating';
  const [activeTab, setActiveTab] = useState<DrawerTab>('process');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const understandingActivity = findActivity(activities, 'understanding_request');
  const searchActivity = findActivity(activities, 'searching_relevant_information');
  const contextActivity = findActivity(activities, 'reviewing_context');
  const modelActivity = findActivity(activities, 'preparing_response', 'preparing_action_plan');

  const filenames = trace?.retrievedFilenames ?? [];
  const isRagRoute = filenames.length > 0 || Boolean(searchActivity);
  const chunkCount =
    searchActivity?.detail?.kind === 'documents_found' ? searchActivity.detail.current : filenames.length;
  const reasoningMs = spanMilliseconds(modelActivity?.startedAt, modelActivity?.completedAt);
  const isMemoryDegraded = contextActivity?.outcome === 'degraded';

  const handleCopyReasoning = () => {
    if (!trace?.reasoning) return;
    void navigator.clipboard?.writeText(trace.reasoning);
    setCopied(true);
  };

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
      <div className="min-h-0 overflow-y-auto p-5">
        {activeTab === 'process' ? (
          <div role="tabpanel" id="trace-panel-process" aria-labelledby="trace-tab-process">
            <ol aria-label="Tiến trình xử lý" className="relative space-y-6">
              {/* Continuous connector line behind the status nodes */}
              <span aria-hidden="true" className="absolute left-3 top-3 bottom-3 w-px bg-[#413b34]" />

              {/* Step 1 — Hiểu yêu cầu */}
              <li className="relative flex gap-3">
                <StatusNode status={stepStatus(understandingActivity, trace)} />
                <div className="min-w-0 flex-1 space-y-2">
                  <p className="text-sm font-medium text-zinc-200">1. Hiểu yêu cầu</p>
                  <div className="flex items-center gap-2 text-xs text-zinc-400">
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
                  </div>
                </div>
              </li>

              {/* Step 2 — Tìm thông tin liên quan */}
              <li className="relative flex gap-3">
                <StatusNode status={stepStatus(searchActivity, trace)} />
                <div className="min-w-0 flex-1 space-y-2">
                  <p className="text-sm font-medium text-zinc-200">2. Tìm thông tin liên quan</p>
                  {isRagRoute ? (
                    <>
                      <p className="text-xs text-zinc-400">
                        Đã tìm thấy{' '}
                        <span className="font-medium text-zinc-200">{chunkCount}</span> đoạn nội dung liên quan
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
                  <p className="text-sm font-medium text-zinc-200">3. Tổng hợp câu trả lời</p>
                  <div className="flex flex-wrap items-center gap-2 text-[11px]">
                    {trace && (
                      <span className="rounded border border-[#6d3e2e] bg-[#38231a] px-2 py-0.5 font-medium text-[#e8a78f]">
                        {trace.model}
                      </span>
                    )}
                    {reasoningMs !== null && (
                      <span className="text-zinc-400">Suy luận trong {formatSecondsVi(reasoningMs)} giây</span>
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
                            className="inline-flex items-center gap-1 rounded border border-[#413b34] bg-[#181715] px-2 py-1 text-[11px] text-zinc-300 transition-colors hover:text-zinc-100"
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
            className="space-y-3"
          >
            <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">
              <BrainCircuit className="h-4 w-4 text-[#e8a78f]" /> Trạng thái bộ nhớ
            </h2>
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
          </section>
        )}
      </div>
    </aside>
  );
}
