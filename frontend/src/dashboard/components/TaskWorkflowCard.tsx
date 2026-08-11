import React, { useState } from 'react';
import type { TaskWorkflow } from '../types';
import type { ErrorBody, StepView } from '../../modules/work-intake/types';

interface TaskWorkflowCardProps {
  workflow: TaskWorkflow;
  onApprove?: (taskId: string) => Promise<void> | void;
  onRevise?: (taskId: string, feedback: string) => Promise<void> | void;
  onRetry?: (taskId: string, stepId: string) => void;
}

/** Latest step error the server reported, so retry only shows when it is allowed. */
function stepError(workflow: TaskWorkflow, stepId: string): ErrorBody | null {
  const events = workflow.events.filter(
    (event) => event.aggregate_type === 'STEP' && event.aggregate_id === stepId
  );
  return events[events.length - 1]?.error ?? null;
}

const STATE_TONE: Record<string, string> = {
  SUCCEEDED: 'text-emerald-400',
  PARTIAL: 'text-amber-300',
  FAILED: 'text-red-400',
  TIMED_OUT: 'text-red-400',
  CANCELLED: 'text-zinc-500',
  SKIPPED: 'text-zinc-500',
};

export const TaskWorkflowCard: React.FC<TaskWorkflowCardProps> = ({
  workflow,
  onApprove,
  onRevise,
  onRetry,
}) => {
  const [feedback, setFeedback] = useState('');
  const [pendingAction, setPendingAction] = useState<'approve' | 'revise' | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const detail = workflow.detail;
  const plan = detail?.plan ?? null;
  // The plan preview lists what the server planned, never a local guess; step
  // state comes from the task view because it is the one that keeps advancing.
  const steps: StepView[] = detail?.steps ?? plan?.steps ?? [];
  const awaitingApproval = Boolean(plan) && plan?.approved === false;
  const completed = detail?.task.status === 'COMPLETED';
  const failed = detail?.task.status === 'FAILED';

  const runPlanAction = async (
    action: 'approve' | 'revise',
    operation: () => Promise<void> | void
  ) => {
    setPendingAction(action);
    setActionMessage(null);
    setActionError(null);
    try {
      await operation();
      setActionMessage(
        action === 'approve'
          ? 'Plan đã được phê duyệt — đang thực hiện.'
          : 'Plan đã được cập nhật — chờ bạn phê duyệt lại.'
      );
      if (action === 'revise') setFeedback('');
    } catch (error) {
      setActionError(
        error instanceof Error ? error.message : 'Không thể cập nhật plan.'
      );
    } finally {
      setPendingAction(null);
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-[#38342f] bg-[#24221f] px-3 py-2.5 text-[11px]">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`h-2 w-2 rounded-full ${
            completed
              ? 'bg-emerald-400'
              : failed
                ? 'bg-red-400'
                : 'animate-pulse bg-sky-400'
          }`}
        />
        <span className="text-zinc-200">{workflow.phase}</span>
      </div>

      {workflow.connectionState === 'unavailable' && (
        <p className="mt-2 text-amber-300">
          Không thể cập nhật tiến độ từ backend.
        </p>
      )}

      {(steps.length > 0 || plan) && (
        <details className="mt-2 rounded-lg border border-[#38342f] bg-[#211f1c]">
          <summary className="cursor-pointer px-2.5 py-2 text-zinc-500 hover:text-zinc-300">
            Chi tiết các bước
          </summary>
          <div className="border-t border-[#38342f] px-2.5 py-2">
            <div className="mb-2 flex flex-wrap gap-2 text-zinc-600">
              <span className="font-mono">{workflow.taskId}</span>
              {plan && <span>plan v{plan.plan_version}</span>}
            </div>
            <ul className="space-y-1">
          {steps.map((step) => {
            const error = stepError(workflow, step.step_id);
            return (
              <li
                key={step.step_id}
                className="flex flex-wrap items-center gap-2 rounded-lg bg-[#2b2925] px-2 py-1.5"
              >
                <span className="font-mono text-zinc-300">{step.step_id}</span>
                <span className="text-zinc-500">{step.capability_id}</span>
                <span className={STATE_TONE[step.state] ?? 'text-zinc-400'}>
                  {step.state}
                </span>
                {error && <span className="text-zinc-500">{error.user_message}</span>}
                {step.state === 'FAILED' && error?.retryable && onRetry && (
                  <button
                    type="button"
                    onClick={() => onRetry(workflow.taskId, step.step_id)}
                    className="rounded-md border border-[#48433d] px-2 py-0.5 text-zinc-200 hover:bg-[#38342f]"
                  >
                    Thử lại
                  </button>
                )}
              </li>
            );
          })}
            </ul>
          </div>
        </details>
      )}

      {awaitingApproval && (
        <div className="mt-2 space-y-2">
          {pendingAction && (
            <p role="status" className="text-sky-300">
              {pendingAction === 'approve'
                ? 'Đang phê duyệt plan…'
                : 'Đang tạo plan cập nhật…'}
            </p>
          )}
          {actionMessage && (
            <p role="status" className="text-emerald-300">
              {actionMessage}
            </p>
          )}
          {actionError && (
            <p role="alert" className="text-rose-300">
              {actionError}
            </p>
          )}
          <label className="block text-zinc-500" htmlFor={`feedback-${workflow.taskId}`}>
            Góp ý cho plan
          </label>
          <textarea
            id={`feedback-${workflow.taskId}`}
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            rows={2}
            disabled={pendingAction !== null}
            className="w-full rounded-lg border border-[#48433d] bg-[#1f1d1a] px-2 py-1.5 text-zinc-200"
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={pendingAction !== null || !onApprove}
              onClick={() =>
                void runPlanAction('approve', () => onApprove?.(workflow.taskId))
              }
              className="rounded-md bg-[#d97757] px-2.5 py-1 text-[#1b1a17] font-medium disabled:opacity-40"
            >
              Phê duyệt plan
            </button>
            <button
              type="button"
              disabled={pendingAction !== null || !feedback.trim() || !onRevise}
              onClick={() =>
                void runPlanAction('revise', () =>
                  onRevise?.(workflow.taskId, feedback.trim())
                )
              }
              className="rounded-md border border-[#48433d] px-2.5 py-1 text-zinc-200 disabled:opacity-40"
            >
              Yêu cầu chỉnh sửa
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
