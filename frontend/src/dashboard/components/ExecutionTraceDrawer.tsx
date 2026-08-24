import { X, BrainCircuit, Files, Check, LoaderCircle, Circle, AlertCircle, Layers } from 'lucide-react';
import type { ChatActivity, ChatExecutionTrace, ChatGenerationStatus } from '../types';

export interface ExecutionTraceDrawerProps {
  trace?: ChatExecutionTrace;
  activities?: ChatActivity[];
  generationStatus?: ChatGenerationStatus;
  onClose: () => void;
}

const ACTIVITY_LABELS: Record<string, string> = {
  understanding_request: 'Hiểu yêu cầu',
  reviewing_context: 'Xem lại ngữ cảnh liên quan',
  searching_relevant_information: 'Tìm thông tin liên quan',
  preparing_response: 'Tổng hợp câu trả lời',
  preparing_action_plan: 'Chuẩn bị kế hoạch hành động',
  checking_mail: 'Kiểm tra hộp thư',
  processing_email: 'Phân tích email liên quan',
  preparing_mail_results: 'Chuẩn bị kết quả',
};

const ACTIVITY_DESCRIPTIONS: Record<string, string> = {
  understanding_request: 'Phân tích ý định của câu hỏi và xác định luồng định tuyến (Router).',
  reviewing_context: 'Đối chiếu ngữ cảnh hội thoại (Episodic Memory) và hồ sơ dự án.',
  searching_relevant_information: 'Truy xuất ngữ nghĩa (Hybrid RAG) các tài liệu dự án phù hợp.',
  preparing_response: 'Mô hình AI tổng hợp dữ liệu và sinh câu trả lời hoàn chỉnh.',
  preparing_action_plan: 'Mô hình AI phân tích và cấu trúc danh sách công việc cần thực hiện.',
  checking_mail: 'Kiểm tra và nạp danh sách email từ hộp thư của bạn.',
  processing_email: 'Đọc và trích xuất nội dung từ các email được chọn.',
  preparing_mail_results: 'Tổng hợp kết quả phân tích email thành kế hoạch công việc.',
};

function isModelReasoningStep(code: string): boolean {
  return code === 'preparing_response' || code === 'preparing_action_plan';
}

function activityDetailText(activity: ChatActivity): string | null {
  const detail = activity.detail;
  if (activity.outcome === 'no_results') return 'Không tìm thấy tài liệu phù hợp';
  if (activity.outcome === 'degraded') return 'Một phần dữ liệu hiện không khả dụng';
  if (!detail) return null;
  if (detail.kind === 'documents_found') return `Tìm thấy ${detail.current} tài liệu liên quan`;
  if (detail.kind === 'emails_processed') {
    return detail.total === undefined
      ? `Đã xử lý ${detail.current} email`
      : `Đã xử lý ${detail.current}/${detail.total} email`;
  }
  if (detail.kind === 'action_items_prepared') return `Đã chuẩn bị ${detail.current} công việc`;
  return null;
}

export function ExecutionTraceDrawer({
  trace,
  activities,
  generationStatus,
  onClose,
}: ExecutionTraceDrawerProps) {
  const isGenerating = generationStatus === 'generating';
  const hasActivities = Boolean(activities && activities.length > 0);

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

      {/* Drawer Content */}
      <div className="min-h-0 space-y-6 overflow-y-auto p-5">
        {/* Section 1: Per-Step Activity & Reasoning */}
        {hasActivities ? (
          <section className="space-y-4">
            <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">
              <BrainCircuit className="h-4 w-4 text-[#e8a78f]" /> Tiến trình xử lý theo bước
            </h2>

            <ol className="space-y-3" aria-label="Danh sách các bước xử lý">
              {activities!.map((activity, index) => {
                const isModelStep = isModelReasoningStep(activity.code);
                const label = ACTIVITY_LABELS[activity.code] ?? activity.code;
                const defaultDesc = ACTIVITY_DESCRIPTIONS[activity.code] ?? '';
                const detail = activityDetailText(activity);
                const isStepRunning = activity.status === 'running';
                const isStepCompleted = activity.status === 'completed';
                const isStepFailed = activity.status === 'failed' || activity.status === 'cancelled';

                return (
                  <li
                    key={`${activity.code}-${index}`}
                    className="rounded-lg border border-[#413b34] bg-[#181715] p-3.5 space-y-2.5 transition-all"
                  >
                    {/* Step Header */}
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        {isStepCompleted ? (
                          <Check className="h-4 w-4 text-emerald-400 shrink-0" />
                        ) : isStepRunning ? (
                          <LoaderCircle className="h-4 w-4 animate-spin text-[#e8a78f] shrink-0" />
                        ) : isStepFailed ? (
                          <AlertCircle className="h-4 w-4 text-rose-400 shrink-0" />
                        ) : (
                          <Circle className="h-4 w-4 text-zinc-600 shrink-0" />
                        )}
                        <span className="text-sm font-medium text-zinc-200">
                          {index + 1}. {label}
                        </span>
                      </div>

                      {/* Source tag */}
                      {isModelStep ? (
                        <span className="rounded border border-[#6d3e2e] bg-[#38231a] px-2 py-0.5 text-[11px] font-medium text-[#e8a78f] shrink-0">
                          Lập luận mô hình
                        </span>
                      ) : (
                        <span className="rounded border border-[#413b34] bg-[#24211d] px-2 py-0.5 text-[11px] font-medium text-zinc-400 shrink-0">
                          Hệ thống
                        </span>
                      )}
                    </div>

                    {/* Step Description / Detail */}
                    <p className="text-xs text-zinc-400 leading-relaxed pl-6">
                      {defaultDesc}
                      {detail && <span className="block mt-1 font-medium text-zinc-300">└─ {detail}</span>}
                    </p>

                    {/* Step 1 System Detail Card */}
                    {activity.code === 'understanding_request' && (
                      <div className="mt-2 pl-6">
                        <div className="rounded border border-[#38332c] bg-[#141311] px-3 py-2 text-xs">
                          <div className="flex items-center justify-between text-zinc-400">
                            <span>Phân tích yêu cầu:</span>
                            <span className="font-medium text-zinc-200">
                              {trace && trace.retrievedFilenames.length > 0 ? 'RAG' : 'Direct'}
                            </span>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Step 2 System Detail Card */}
                    {activity.code === 'reviewing_context' && (
                      <div className="mt-2 pl-6">
                        <div className="rounded border border-[#38332c] bg-[#141311] px-3 py-2 text-xs space-y-1">
                          <div className="flex items-center justify-between text-zinc-400">
                            <span>Bộ nhớ ngữ cảnh:</span>
                            <span className="font-medium text-zinc-200">Episodic & Working Memory</span>
                          </div>
                          <div className="flex items-center justify-between text-zinc-400">
                            <span>Trạng thái ngữ cảnh:</span>
                            <span className={activity.outcome === 'degraded' ? 'text-amber-400 font-medium' : 'text-emerald-400/90 font-medium'}>
                              {activity.outcome === 'degraded' ? 'Một phần suy giảm' : 'Sẵn sàng & Đồng bộ'}
                            </span>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Step 3 System Detail Card */}
                    {activity.code === 'searching_relevant_information' && trace && trace.retrievedFilenames.length > 0 && (
                      <div className="mt-2 pl-6">
                        <div className="rounded border border-[#38332c] bg-[#141311] px-3 py-2 text-xs space-y-1.5">
                          <div className="flex items-center justify-between text-zinc-400">
                            <span>Tài liệu đã truy xuất:</span>
                            <span className="font-medium text-zinc-300">{trace.retrievedFilenames.length} tệp</span>
                          </div>
                          <ul className="flex flex-wrap gap-1.5 pt-0.5">
                            {trace.retrievedFilenames.map((filename) => (
                              <li
                                key={filename}
                                className="inline-flex items-center gap-1 rounded border border-[#413b34] bg-[#201e1b] px-2 py-0.5 text-xs text-zinc-300 font-mono"
                              >
                                <Files className="h-3 w-3 text-[#e8a78f] shrink-0" />
                                <span className="truncate max-w-[240px]">{filename}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    )}

                    {/* Model Reasoning / Chain of Thought content under model step */}
                    {isModelStep && (
                      <div className="mt-2 pl-6 space-y-2">
                        {trace?.reasoning ? (
                          <div className="space-y-1.5">
                            <div className="flex items-center justify-between">
                              <span className="text-[11px] font-semibold text-zinc-300">Chuỗi suy luận (Chain of Thought):</span>
                              {trace.reasoningTruncated && (
                                <span className="text-[10px] text-amber-300">Đã rút gọn</span>
                              )}
                            </div>
                            <pre className="whitespace-pre-wrap break-words rounded-lg border border-[#413b34] bg-[#121110] p-3 font-mono text-xs leading-5 text-zinc-300 max-h-96 overflow-y-auto">
                              {trace.reasoning}
                            </pre>
                            {trace.reasoningTruncated && (
                              <p className="text-xs text-amber-300">Reasoning đã được rút gọn để lưu an toàn.</p>
                            )}
                          </div>
                        ) : trace?.mode === 'fast' ? (
                          <div className="rounded border border-[#413b34] bg-[#141312] p-2.5 text-xs text-zinc-400">
                            <span className="font-semibold text-zinc-300">Chế độ Nhanh:</span> Mô hình sinh câu trả lời trực tiếp không qua bước suy luận sâu (Thinking disabled).
                          </div>
                        ) : isStepRunning ? (
                          <div className="rounded border border-[#52382c] bg-[#1e1713] p-2.5 text-xs text-[#e8a78f] animate-pulse">
                            Đang chờ mô hình thực hiện suy luận...
                          </div>
                        ) : (
                          <p className="text-xs text-zinc-500">Nhà cung cấp không trả reasoning cho lượt này.</p>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          </section>
        ) : (
          /* Fallback when no activities array is supplied (e.g. minimal trace mode) */
          <>
            <section>
              <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-200">
                <BrainCircuit className="h-4 w-4 text-[#e8a78f]" /> Lập luận mô hình
              </h2>
              {trace?.reasoning ? (
                <pre className="mt-3 whitespace-pre-wrap break-words rounded-lg border border-[#413b34] bg-[#181715] p-3 font-sans text-xs leading-5 text-zinc-300">
                  {trace.reasoning}
                </pre>
              ) : (
                <p className="mt-2 text-sm text-zinc-500">Nhà cung cấp không trả reasoning cho lượt này.</p>
              )}
              {trace?.reasoningTruncated && (
                <p className="mt-2 text-xs text-amber-300">Reasoning đã được rút gọn để lưu an toàn.</p>
              )}
            </section>

            <section>
              <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-200">
                <Files className="h-4 w-4 text-[#e8a78f]" /> Tài liệu đã truy xuất
              </h2>
              {trace?.retrievedFilenames.length ? (
                <ul className="mt-3 space-y-2">
                  {trace.retrievedFilenames.map((filename) => (
                    <li key={filename} className="rounded-md border border-[#413b34] bg-[#181715] px-3 py-2 text-sm text-zinc-300 font-mono">
                      {filename}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-zinc-500">Không có tệp nào được truy xuất.</p>
              )}
            </section>
          </>
        )}
      </div>
    </aside>
  );
}
