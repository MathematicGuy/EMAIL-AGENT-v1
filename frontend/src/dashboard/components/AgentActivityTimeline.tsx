import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Circle, LoaderCircle, X } from 'lucide-react';
import type { ChatActivity, ChatGenerationStatus } from '../types';

const LABELS: Record<ChatActivity['code'], string> = {
  understanding_request: 'Hiểu yêu cầu',
  searching_relevant_information: 'Tìm thông tin liên quan',
  reviewing_context: 'Xem lại ngữ cảnh liên quan',
  preparing_response: 'Tổng hợp câu trả lời',
  preparing_action_plan: 'Chuẩn bị kế hoạch hành động',
  checking_mail: 'Kiểm tra hộp thư của bạn',
  processing_email: 'Phân tích email liên quan',
  preparing_mail_results: 'Chuẩn bị kết quả',
};

function detailLabel(activity: ChatActivity): string | null {
  const detail = activity.detail;
  if (activity.outcome === 'no_results') return 'Không tìm thấy thông tin phù hợp';
  if (activity.outcome === 'degraded') return 'Một phần thông tin hiện không khả dụng';
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

function elapsed(activities: ChatActivity[], completedAt?: string): string | null {
  const first = activities.find((item) => item.startedAt)?.startedAt;
  const terminalAt = completedAt ?? activities.findLast((item) => item.completedAt)?.completedAt;
  if (!first || !terminalAt) return null;
  const milliseconds = new Date(terminalAt).getTime() - new Date(first).getTime();
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return null;
  return `${(milliseconds / 1000).toFixed(1).replace('.', ',')} giây`;
}

export const AgentActivityTimeline: React.FC<{
  activities: ChatActivity[];
  generationStatus?: ChatGenerationStatus;
  completedAt?: string;
}> = ({ activities, generationStatus, completedAt }) => {
  const isLive = generationStatus === 'generating';
  const beganLive = useRef(isLive);
  const [expanded, setExpanded] = useState(isLive);

  useEffect(() => {
    if (generationStatus === 'completed' && beganLive.current) {
      const timer = window.setTimeout(() => setExpanded(false), 800);
      return () => window.clearTimeout(timer);
    }
  }, [generationStatus]);

  const completed = activities.filter((item) => item.status === 'completed').length;
  const animatedIndex = activities.findLastIndex((item) => item.status === 'running');
  const duration = elapsed(activities, completedAt);
  const interrupted = generationStatus === 'interrupted';
  const summary = useMemo(() => {
    const suffix = [`Đã xong ${completed} bước`, duration].filter(Boolean).join(' · ');
    if (generationStatus === 'failed' || generationStatus === 'usage_limit_reached' || generationStatus === 'temporarily_rate_limited') {
      return `Không thể hoàn tất${suffix ? ` · ${suffix}` : ''}`;
    }
    if (generationStatus === 'cancelled') return `Đã hủy${suffix ? ` · ${suffix}` : ''}`;
    if (interrupted) return `Bị gián đoạn${completed ? ` · Đã xong ${completed} bước` : ''}`;
    return `Hoàn tất ${completed} bước${duration ? ` · ${duration}` : ''}`;
  }, [completed, duration, generationStatus, interrupted]);

  if (activities.length === 0) return null;
  return (
    <section className="mb-4 rounded-xl border border-[#413b34] bg-[#24211d]" aria-label="Tiến độ xử lý">
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm text-zinc-200"
      >
        <span className="flex items-center gap-2 font-medium">
          {isLive ? <span className="text-[#e8a78f]" aria-hidden="true">✦</span> : <Check className="h-4 w-4 text-emerald-400" />}
          {isLive ? 'Đang làm việc' : summary}
        </span>
        <span className="flex items-center gap-2 text-xs text-zinc-400">
          {!isLive && (expanded ? 'Ẩn hoạt động' : 'Xem hoạt động')}
          <ChevronDown className={`h-4 w-4 transition-transform motion-reduce:transition-none ${expanded ? 'rotate-180' : ''}`} />
        </span>
      </button>
      {expanded && (
        <ol className="border-t border-[#3a352f] px-4 py-3" aria-live="polite" aria-atomic="false">
          {activities.map((activity, index) => {
            const running = index === animatedIndex && !interrupted;
            const detail = detailLabel(activity);
            return (
              <li key={activity.code} className="relative flex gap-3 pb-4 last:pb-0">
                {index < activities.length - 1 && <span className="absolute left-[7px] top-5 h-[calc(100%-0.5rem)] w-px bg-[#514a42]" />}
                <span className="relative z-10 mt-0.5 bg-[#24211d]" aria-hidden="true">
                  {activity.status === 'completed' ? <Check className="h-4 w-4 text-emerald-400" />
                    : running ? <LoaderCircle className="h-4 w-4 animate-spin text-[#e8a78f] motion-reduce:animate-none" />
                      : activity.status === 'failed' || activity.status === 'cancelled' || (activity.status === 'running' && interrupted)
                        ? <X className="h-4 w-4 text-rose-400" />
                        : <Circle className="h-4 w-4 text-zinc-600" />}
                </span>
                <div className={activity.status === 'pending' || activity.status === 'skipped' ? 'text-zinc-500' : 'text-zinc-200'}>
                  <div className="text-sm">{LABELS[activity.code]}</div>
                  {detail && <div className="mt-1 text-xs text-zinc-400">└─ {detail}</div>}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
};
