import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Pause,
  Play,
  Send,
  Workflow,
  X
} from 'lucide-react';
import {
  approveAction,
  approvePlan,
  getTask,
  patchPlan,
  sendControlCommand,
  submitQuery,
  WorkIntakeApiError
} from './api';
import type { WorkIntakeScope } from './api';
import type {
  CalendarEventTime,
  CalendarSourceCitation,
  ControlAction,
  PendingActionApproval,
  PlanStep,
  StepStatus,
  TaskDetail,
  TaskStatus
} from './types';
import { DONE_STEP_STATES, isTerminalStatus } from './types';

interface WorkIntakePanelProps {
  isOpen: boolean;
  onClose: () => void;
}

interface PanelError {
  code: string;
  message: string;
}

const DEMO_SCOPE: WorkIntakeScope = {
  actorId: 'demo-user',
  projectId: 'demo-project',
  workspaceId: 'demo-workspace'
};

const POLL_INTERVAL_MS = 1500;

function toPanelError(cause: unknown, fallback: string): PanelError {
  return cause instanceof WorkIntakeApiError
    ? { code: cause.code, message: cause.message }
    : { code: 'NETWORK_ERROR', message: fallback };
}

const STEP_STATE_CLASS: Partial<Record<StepStatus, string>> = {
  SUCCEEDED: 'text-emerald-400',
  RUNNING: 'text-sky-400',
  DISPATCHED: 'text-sky-400',
  READY: 'text-sky-400',
  PARTIAL: 'text-amber-400',
  WAITING_INPUT: 'text-amber-400',
  WAITING_APPROVAL: 'text-amber-400',
  FAILED: 'text-red-400',
  TIMED_OUT: 'text-red-400',
  BLOCKED: 'text-red-400',
  CANCELLED: 'text-zinc-500',
  SKIPPED: 'text-zinc-500'
};

function statusClass(status: TaskStatus): string {
  if (status === 'COMPLETED') return 'text-emerald-400';
  if (status === 'FAILED' || status === 'CANCELLED') return 'text-red-400';
  if (status === 'PARTIAL' || status === 'AWAITING_PLAN_APPROVAL') {
    return 'text-amber-400';
  }
  return 'text-sky-400';
}

/** One edge of the event, rendered exactly as the executor will send it. */
function edgeLabel(edge: CalendarEventTime | null | undefined): string {
  if (!edge) return '—';
  return `${edge.date_time ?? edge.date ?? '—'} (${edge.time_zone})`;
}

interface ActionApprovalCardProps {
  pending: PendingActionApproval;
  busy: boolean;
  onDecide: (decision: 'approved' | 'denied') => void;
}

/**
 * The one screen a human reads before an external side effect happens.
 *
 * Everything shown comes from the server's preview; nothing is inferred here.
 * `account_label` and `calendar_id` are both rendered because they are
 * configured separately and no code compares them — the approver is the only
 * one who can notice a label naming a calendar other than the target.
 */
function ActionApprovalCard({ pending, busy, onDecide }: ActionApprovalCardProps) {
  const preview = pending.preview;
  const citations = preview.source_citations ?? [];
  const sourcesByField = new Map(
    citations.map((citation) => [citation.field, citation])
  );
  const fields: { label: string; field: CalendarSourceCitation['field']; value: string }[] = [
    { label: 'Tiêu đề', field: 'summary', value: preview.summary ?? '—' },
    { label: 'Bắt đầu', field: 'start', value: edgeLabel(preview.start) },
    { label: 'Kết thúc', field: 'end', value: edgeLabel(preview.end) },
    { label: 'Địa điểm', field: 'location', value: preview.location ?? '—' }
  ];

  return (
    <section className="rounded-2xl border border-amber-900/50 bg-amber-950/20 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-amber-200">
          Action approval required
        </h3>
        <span className="text-[10px] font-mono text-amber-100/60">
          {preview.capability_id}
        </span>
      </div>
      <p className="mt-1 text-xs text-amber-100/70">
        Không có sự kiện nào được tạo trước khi bạn duyệt. Hết hạn lúc{' '}
        {pending.expires_at}.
      </p>

      <dl className="mt-3 space-y-2 text-xs">
        {fields.map((row) => {
          const citation = sourcesByField.get(row.field);
          return (
            <div key={row.field} className="flex flex-col gap-0.5">
              <dt className="text-[10px] uppercase tracking-wide text-zinc-500">
                {row.label}
              </dt>
              <dd className="text-zinc-100">{row.value}</dd>
              {citation ? (
                <span className="text-[10px] text-zinc-400">
                  {citation.assumed ? (
                    <span className="text-amber-300">
                      Giả định (không có trong nguồn) ·{' '}
                    </span>
                  ) : null}
                  Nguồn [{citation.result_index}]{' '}
                  <a
                    href={citation.url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline decoration-dotted hover:text-zinc-200"
                  >
                    {citation.url}
                  </a>
                </span>
              ) : (
                <span className="text-[10px] text-zinc-500">Không có nguồn</span>
              )}
            </div>
          );
        })}
      </dl>

      <div className="mt-3 space-y-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-canvas)] p-3 text-[10px] text-zinc-400">
        <p>
          Tài khoản: <span className="text-zinc-200">{preview.account_label}</span>
        </p>
        <p>
          Lịch đích:{' '}
          <span className="font-mono text-zinc-200">
            {preview.calendar_id ?? '—'}
          </span>
        </p>
        <p className="font-mono">payload {pending.payload_hash}</p>
      </div>

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => onDecide('approved')}
          disabled={busy}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-semibold text-zinc-950 disabled:opacity-50"
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          Approve action
        </button>
        <button
          type="button"
          onClick={() => onDecide('denied')}
          disabled={busy}
          className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
        >
          <Ban className="h-3.5 w-3.5" />
          Deny
        </button>
      </div>
    </section>
  );
}

interface PlanStepEditorProps {
  step: PlanStep;
  edits: Partial<PlanStep>;
  onChange: (updates: Partial<PlanStep>) => void;
}

function PlanStepEditor({ step, edits, onChange }: PlanStepEditorProps) {
  const timeout_seconds = edits.timeout_seconds ?? step.timeout_seconds;
  const required = edits.required ?? step.required;
  const allow_partial = edits.allow_partial ?? step.allow_partial;
  const approval_required = edits.approval_required ?? step.approval_required;

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900 p-3">
      <p className="text-xs font-semibold text-zinc-100">{step.step_id}</p>
      <p className="mt-1 text-[11px] text-zinc-500">
        {step.assigned_module} · {step.capability_id}@{step.capability_version}
      </p>

      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col text-xs text-zinc-400">
          Timeout (seconds)
          <input
            type="number"
            value={timeout_seconds}
            onChange={(e) => onChange({ timeout_seconds: parseInt(e.target.value, 10) })}
            className="mt-1 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-100 outline-none focus:border-[var(--color-accent)]"
          />
        </label>

        <div className="flex items-end gap-2">
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={required}
              onChange={(e) => onChange({ required: e.target.checked })}
              className="rounded border border-zinc-600 bg-zinc-950"
            />
            Required
          </label>
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={allow_partial}
              onChange={(e) => onChange({ allow_partial: e.target.checked })}
              className="rounded border border-zinc-600 bg-zinc-950"
            />
            Allow partial
          </label>
        </div>
      </div>

      <label className="mt-2 flex items-center gap-2 text-xs text-zinc-400">
        <input
          type="checkbox"
          checked={approval_required}
          onChange={(e) => onChange({ approval_required: e.target.checked })}
          className="rounded border border-zinc-600 bg-zinc-950"
        />
        Approval required
      </label>

      {step.depends_on.length > 0 && (
        <div className="mt-2">
          <p className="text-[10px] text-zinc-600">
            Depends on:{' '}
            {step.depends_on.map((d) => d.step_id).join(', ')}
          </p>
        </div>
      )}
    </div>
  );
}

export function WorkIntakePanel({ isOpen, onClose }: WorkIntakePanelProps) {
  const [query, setQuery] = useState('');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PanelError | null>(null);
  const [templateHashInput, setTemplateHashInput] = useState<string | null>(null);
  const [isRevising, setIsRevising] = useState(false);
  const [patchEdits, setPatchEdits] = useState<Record<string, Partial<PlanStep>>>({});
  const [patchReason, setPatchReason] = useState('');

  const task = detail?.task ?? null;
  const run = detail?.run ?? null;
  const plan = detail?.plan ?? null;
  const finalResponse = detail?.final_response ?? null;
  const pendingAction = detail?.action_approval ?? null;
  const isTerminal = task ? isTerminalStatus(task.status) : false;
  const planHash = plan?.query_template_hash ?? null;
  // The server value seeds the field; an operator edit takes over from there.
  const templateHash = templateHashInput ?? planHash ?? '';

  // Build a runtime-state lookup from StepView (polled).
  // Use plan_steps as the canonical order source; fall back to detail.steps.
  const stateByStepId = new Map(
    (plan?.steps ?? detail?.steps ?? []).map((s) => [s.step_id, s])
  );
  // steps: merged list preserving plan_steps order, enriched with runtime state.
  const planSteps = plan?.plan_steps ?? [];
  const steps =
    planSteps.length > 0
      ? planSteps.map((ps) => ({
          ...ps,
          state: stateByStepId.get(ps.step_id)?.state ?? ('PENDING' as const),
          current_attempt: stateByStepId.get(ps.step_id)?.current_attempt ?? 0,
          progress_percent: stateByStepId.get(ps.step_id)?.progress_percent ?? null
        }))
      : (plan?.steps ?? detail?.steps ?? []).map((sv) => ({ ...sv, input_refs: [], expected_output_schema: {} }));

  // ponytail: progress is derived from step states instead of consuming
  // GET /v1/tasks/{id}/events — the polled task view already carries every
  // state this panel renders, so the event stream stays unused.
  const doneSteps = steps.filter((step) =>
    DONE_STEP_STATES.includes(step.state)
  ).length;
  const progressPercent =
    steps.length === 0 ? 0 : Math.round((doneSteps / steps.length) * 100);

  useEffect(() => {
    if (!isOpen || !taskId || isTerminal) return;
    let cancelled = false;
    const tick = () => {
      getTask({ taskId, scope: DEMO_SCOPE })
        .then((next) => {
          if (!cancelled) setDetail(next);
        })
        .catch((cause: unknown) => {
          if (!cancelled) {
            setError(toPanelError(cause, 'Không thể tải trạng thái task.'));
          }
        });
    };
    tick();
    const timer = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [isOpen, taskId, isTerminal]);

  if (!isOpen) return null;

  const runQuery = async () => {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    setDetail(null);
    setTaskId(null);
    try {
      const created = await submitQuery({
        query: query.trim(),
        scope: DEMO_SCOPE
      });
      setDetail({ task: created, run: null, plan: null, steps: [] });
      setTaskId(created.task_id);
    } catch (cause) {
      setError(toPanelError(cause, 'Không thể gửi yêu cầu.'));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!taskId || !plan || !templateHash.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const approved = await approvePlan({
        taskId,
        planVersion: plan.plan_version,
        queryTemplateHash: templateHash.trim(),
        approvalMode: plan.approval_mode ?? 'REQUIRE_INTERACTIVE',
        scope: DEMO_SCOPE
      });
      setDetail((current) =>
        current ? { ...current, plan: approved, steps: approved.steps } : current
      );
    } catch (cause) {
      setError(toPanelError(cause, 'Không thể duyệt plan.'));
    } finally {
      setBusy(false);
    }
  };

  const decideAction = async (choice: 'approved' | 'denied') => {
    if (!taskId || !pendingAction) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await approveAction({
        taskId,
        pending: pendingAction,
        decision: choice,
        scope: DEMO_SCOPE
      });
      // The decision clears the park, so the whole detail is re-read rather than
      // patched: the step state and the pending action both changed.
      setDetail((current) =>
        current ? { ...current, task: updated, action_approval: null } : current
      );
    } catch (cause) {
      setError(toPanelError(cause, 'Không thể gửi quyết định cho action.'));
    } finally {
      setBusy(false);
    }
  };

  const revise = async () => {
    if (!taskId || !plan) return;
    // Merge edits with original plan_steps to create final steps
    const finalSteps: PlanStep[] = plan.plan_steps.map((step) => ({
      ...step,
      ...(patchEdits[step.step_id] ?? {})
    }));
    setBusy(true);
    setError(null);
    try {
      const patched = await patchPlan({
        taskId,
        expectedPlanVersion: plan.plan_version,
        steps: finalSteps,
        reason: patchReason.trim() || null,
        scope: DEMO_SCOPE
      });
      setDetail((current) =>
        current ? { ...current, plan: patched, steps: patched.steps } : current
      );
      setIsRevising(false);
      setPatchEdits({});
    } catch (cause) {
      setError(toPanelError(cause, 'Không thể sửa plan.'));
    } finally {
      setBusy(false);
    }
  };

  const control = async (action: ControlAction) => {
    if (!taskId || !task) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await sendControlCommand({
        taskId,
        action,
        expectedStateVersion: task.state_version,
        scope: DEMO_SCOPE
      });
      setDetail((current) =>
        current ? { ...current, task: updated } : current
      );
    } catch (cause) {
      setError(toPanelError(cause, 'Không thể gửi control command.'));
    } finally {
      setBusy(false);
    }
  };

  const awaitingApproval = task?.status === 'AWAITING_PLAN_APPROVAL';
  const canPause =
    task !== null && task.status === 'RUNNING' && !task.pause_requested;
  const canResume =
    task !== null && (task.status === 'PAUSED' || task.pause_requested);
  const canCancel = task !== null && !isTerminal;

  const contextDegraded =
    finalResponse?.context_degraded ?? run?.context_degraded ?? false;
  const memoryDegraded =
    finalResponse?.memory_degraded ?? run?.memory_degraded ?? false;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/55"
      role="dialog"
      aria-modal="true"
      aria-label="Work intake"
    >
      <section className="flex h-full w-full max-w-3xl flex-col border-l border-[var(--color-border)] bg-[var(--color-surface)] shadow-2xl">
        <header className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-4">
          <div className="flex items-center gap-3">
            <span className="rounded-xl bg-[var(--color-accent-soft)] p-2 text-[var(--color-accent)]">
              <Workflow className="h-5 w-5" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-zinc-100">
                Work Intake &amp; Agent Orchestrator
              </h2>
              <p className="text-xs text-zinc-500">
                {task ? `${task.task_id} · run ${task.run_id}` : 'Chưa có task'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close work intake panel"
            className="rounded-lg p-2 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-2 focus-visible:outline-[var(--color-accent)]"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        {error && (
          <div className="mx-5 mt-4 flex items-start gap-2 rounded-xl border border-red-900/50 bg-red-950/30 p-3 text-xs text-red-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              <span className="font-mono text-red-300">{error.code}</span>
              {': '}
              {error.message}
            </span>
          </div>
        )}

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-5">
          <form
            className="space-y-2"
            onSubmit={(event) => {
              event.preventDefault();
              void runQuery();
            }}
          >
            <label className="text-xs text-zinc-400" htmlFor="work-intake-query">
              Yêu cầu công việc
            </label>
            <textarea
              id="work-intake-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Ví dụ: tổng hợp doanh thu quý 2 từ workspace và lập báo cáo."
              className="min-h-24 w-full rounded-xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-3 text-xs text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-[var(--color-accent)]"
            />
            <button
              type="submit"
              disabled={busy || !query.trim()}
              className="flex items-center gap-2 rounded-xl bg-[var(--color-accent)] px-4 py-2 text-xs font-semibold text-zinc-950 disabled:opacity-50"
            >
              <Send className="h-3.5 w-3.5" />
              Submit request
            </button>
          </form>

          {task && (
            <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-zinc-500">Task status</span>
                  <span className={`font-semibold ${statusClass(task.status)}`}>
                    {task.status}
                  </span>
                  {task.pause_requested && (
                    <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-[10px] text-amber-300">
                      pause requested
                    </span>
                  )}
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => void control('PAUSE')}
                    disabled={busy || !canPause}
                    className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-30"
                  >
                    <Pause className="h-3.5 w-3.5" />
                    Pause
                  </button>
                  <button
                    type="button"
                    onClick={() => void control('RESUME')}
                    disabled={busy || !canResume}
                    className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800 disabled:opacity-30"
                  >
                    <Play className="h-3.5 w-3.5" />
                    Resume
                  </button>
                  <button
                    type="button"
                    onClick={() => void control('CANCEL')}
                    disabled={busy || !canCancel}
                    className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs text-zinc-300 hover:bg-red-950/40 hover:text-red-300 disabled:opacity-30"
                  >
                    <Ban className="h-3.5 w-3.5" />
                    Cancel
                  </button>
                </div>
              </div>

              <div className="mt-3 h-2 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full bg-[var(--color-accent)]"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              <p className="mt-2 text-[10px] text-zinc-500">
                Run progress {progressPercent}% · {doneSteps}/{steps.length} steps
                {run ? ` · replan ${run.replan_count}` : ''}
              </p>
            </section>
          )}

          {plan && (
            <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4">
              <h3 className="text-sm font-semibold text-zinc-100">
                Query interpretation
              </h3>
              <p className="mt-2 text-sm leading-6 text-zinc-200">
                {plan.objective}
              </p>
              <dl className="mt-3 grid gap-3 sm:grid-cols-3">
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-zinc-600">
                    Scope
                  </dt>
                  <dd className="mt-1 space-y-1 text-xs text-zinc-300">
                    {plan.scope.length === 0 ? (
                      <span className="text-zinc-600">—</span>
                    ) : (
                      plan.scope.map((item) => <p key={item}>{item}</p>)
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-zinc-600">
                    Out of scope
                  </dt>
                  <dd className="mt-1 space-y-1 text-xs text-zinc-300">
                    {plan.out_of_scope.length === 0 ? (
                      <span className="text-zinc-600">—</span>
                    ) : (
                      plan.out_of_scope.map((item) => <p key={item}>{item}</p>)
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-zinc-600">
                    Assumptions
                  </dt>
                  <dd className="mt-1 space-y-1 text-xs text-zinc-300">
                    {plan.assumptions.length === 0 ? (
                      <span className="text-zinc-600">—</span>
                    ) : (
                      plan.assumptions.map((item) => <p key={item}>{item}</p>)
                    )}
                  </dd>
                </div>
              </dl>
            </section>
          )}

          {steps.length > 0 && (
            <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-zinc-100">
                  Task graph
                </h3>
                {plan && (
                  <span className="text-[10px] text-zinc-600">
                    plan v{plan.plan_version} ·{' '}
                    {plan.approved ? 'approved' : 'not approved'}
                  </span>
                )}
              </div>
              <ol className="mt-3 space-y-2">
                {steps.map((step, index) => {
                  const isFailed = step.state === 'FAILED' || step.state === 'TIMED_OUT';
                  const inputRefs = ('input_refs' in step ? step.input_refs : []) as Array<{ ref_id: string; source_id?: string | null }>;
                  const outputSchema = ('expected_output_schema' in step ? step.expected_output_schema : null) as Record<string, unknown> | null;
                  const schemaRef = outputSchema?.['$ref'] as string | undefined;
                  return (
                    <li
                      key={step.step_id}
                      className={`rounded-xl border bg-zinc-950 p-3 ${
                        isFailed ? 'border-red-900/60' : 'border-zinc-800'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <p className="text-xs text-zinc-200">
                            <span className="text-zinc-600">{index + 1}.</span>{' '}
                            {step.step_id}
                            {!step.required && (
                              <span className="ml-2 text-[10px] text-zinc-600">optional</span>
                            )}
                          </p>
                          <p className="mt-1 text-[11px] text-zinc-500">
                            {step.assigned_module} · {step.capability_id}@{step.capability_version}
                          </p>
                          {/* Input refs */}
                          {inputRefs.length > 0 && (
                            <div className="mt-2">
                              <p className="text-[10px] uppercase tracking-wide text-zinc-600">Đầu vào</p>
                              <ul className="mt-0.5 space-y-0.5">
                                {inputRefs.map((ref) => (
                                  <li key={ref.ref_id} className="font-mono text-[10px] text-zinc-400">
                                    {ref.source_id ?? ref.ref_id}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                          {/* Expected output schema */}
                          {schemaRef && (
                            <p className="mt-1 text-[10px] text-zinc-600">
                              <span className="text-zinc-700">output schema: </span>
                              <span className="font-mono text-zinc-500">{schemaRef}</span>
                            </p>
                          )}
                          {/* Depends on */}
                          <p className="mt-1 text-[10px] text-zinc-600">
                            depends on:{' '}
                            {step.depends_on.length === 0
                              ? 'none'
                              : step.depends_on.map((d) => d.step_id).join(', ')}
                          </p>
                          {/* Failed step error hint */}
                          {isFailed && (
                            <p className="mt-1.5 text-[10px] text-red-400">
                              ⚠ Bước thất bại — xem Final result bên dưới để biết chi tiết lỗi.
                            </p>
                          )}
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-1">
                          <span
                            className={`text-[10px] font-semibold uppercase tracking-wide ${
                              STEP_STATE_CLASS[step.state] ?? 'text-zinc-400'
                            }`}
                          >
                            {step.state}
                          </span>
                          {'current_attempt' in step && (step as { current_attempt: number }).current_attempt > 1 && (
                            <span className="text-[9px] text-zinc-600">
                              attempt #{(step as { current_attempt: number }).current_attempt}
                            </span>
                          )}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ol>
            </section>
          )}

          {awaitingApproval && plan && (
            <section className="rounded-2xl border border-amber-900/50 bg-amber-950/20 p-4">
              <h3 className="text-sm font-semibold text-amber-200">
                Plan approval required
              </h3>
              <p className="mt-1 text-xs text-amber-100/70">
                Duyệt plan v{plan.plan_version} trước khi orchestrator dispatch
                step đầu tiên.
              </p>
              <label className="mt-3 block text-xs text-zinc-400">
                Query template hash
                <input
                  value={templateHash}
                  onChange={(event) => setTemplateHashInput(event.target.value)}
                  className="mt-1 w-full rounded-xl border border-[var(--color-border)] bg-zinc-950 p-2.5 font-mono text-xs text-zinc-100 outline-none focus:border-[var(--color-accent)]"
                />
              </label>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  onClick={() => void approve()}
                  disabled={busy || !templateHash.trim()}
                  className="flex items-center gap-1.5 rounded-lg bg-[var(--color-accent)] px-3 py-1.5 text-xs font-semibold text-zinc-950 disabled:opacity-50"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Approve plan
                </button>
                <button
                  type="button"
                  onClick={() => setIsRevising((current) => !current)}
                  className="rounded-lg px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800"
                >
                  Revise plan
                </button>
              </div>
              {isRevising && plan && (
                <div className="mt-3 space-y-3">
                  <div className="space-y-3 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
                    {plan.plan_steps.map((step) => (
                      <PlanStepEditor
                        key={step.step_id}
                        step={step}
                        edits={patchEdits[step.step_id] ?? {}}
                        onChange={(updates) =>
                          setPatchEdits((prev) => ({
                            ...prev,
                            [step.step_id]: { ...prev[step.step_id], ...updates }
                          }))
                        }
                      />
                    ))}
                  </div>
                  <label className="block text-xs text-zinc-400">
                    Reason
                    <input
                      value={patchReason}
                      onChange={(event) => setPatchReason(event.target.value)}
                      className="mt-1 w-full rounded-xl border border-[var(--color-border)] bg-zinc-950 p-2.5 text-xs text-zinc-100 outline-none focus:border-[var(--color-accent)]"
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => void revise()}
                    disabled={busy}
                    className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
                  >
                    Submit plan patch
                  </button>
                </div>
              )}
            </section>
          )}

          {pendingAction && (
            <ActionApprovalCard
              pending={pendingAction}
              busy={busy}
              onDecide={(choice) => void decideAction(choice)}
            />
          )}

          {isTerminal && task && (
            <section className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas)] p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-zinc-100">
                  Final result
                </h3>
                <span className={`text-xs font-semibold ${statusClass(task.status)}`}>
                  {finalResponse?.terminal_status ?? task.status}
                </span>
              </div>

              {(contextDegraded || memoryDegraded) && (
                <div className="mt-3 flex flex-wrap gap-2 text-[10px]">
                  {contextDegraded && (
                    <span className="rounded-full bg-amber-950/50 px-2 py-1 text-amber-300">
                      context_degraded
                    </span>
                  )}
                  {memoryDegraded && (
                    <span className="rounded-full bg-amber-950/50 px-2 py-1 text-amber-300">
                      memory_degraded
                    </span>
                  )}
                </div>
              )}

              {finalResponse ? (
                <div className="mt-3 space-y-4 text-xs">
                  <p className="leading-6 text-zinc-200">
                    {finalResponse.summary}
                  </p>
                  {finalResponse.artifact_grounding?.map((item) => (
                    <div
                      key={item.artifact_ref_id}
                      className={`rounded-lg border px-3 py-2 ${
                        item.grounding.status === 'GROUNDED'
                          ? 'border-emerald-900 bg-emerald-950/30 text-emerald-300'
                          : 'border-amber-900 bg-amber-950/30 text-amber-300'
                      }`}
                    >
                      <div className="font-medium">
                        {item.grounding.status === 'GROUNDED' ? '🟢 ' : '🟡 '}
                        {item.grounding.label}
                      </div>
                      {item.grounding.sources.length > 0 && (
                        <ol className="mt-2 space-y-1 text-[11px] text-zinc-300">
                          {item.grounding.sources.map((source) => (
                            <li key={source.reference_id}>
                              [{source.marker}] {source.display_name}
                            </li>
                          ))}
                        </ol>
                      )}
                    </div>
                  ))}

                  <div>
                    <h4 className="text-[10px] uppercase tracking-wide text-zinc-600">
                      Findings
                    </h4>
                    {finalResponse.findings.length === 0 ? (
                      <p className="mt-1 text-zinc-600">Không có finding.</p>
                    ) : (
                      <ul className="mt-1 space-y-1.5">
                        {finalResponse.findings.map((finding) => (
                          <li
                            key={finding.finding_id}
                            className="rounded-lg border border-zinc-800 p-2.5 text-zinc-200"
                          >
                            <span className="mr-2 text-[10px] text-zinc-500">
                              {finding.importance}
                            </span>
                            {finding.statement}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  <div>
                    <h4 className="text-[10px] uppercase tracking-wide text-zinc-600">
                      Artifact &amp; resource refs
                    </h4>
                    <ul className="mt-1 space-y-1 font-mono text-[11px] text-zinc-300">
                      {[
                        ...finalResponse.artifact_refs,
                        ...finalResponse.resource_refs
                      ].map((ref) => (
                        <li key={ref.ref_id}>{ref.ref_id}</li>
                      ))}
                      {finalResponse.artifact_refs.length === 0 &&
                        finalResponse.resource_refs.length === 0 && (
                          <li className="font-sans text-zinc-600">Không có ref.</li>
                        )}
                    </ul>
                  </div>

                  <div>
                    <h4 className="text-[10px] uppercase tracking-wide text-zinc-600">
                      Evidence
                    </h4>
                    <ul className="mt-1 space-y-1 text-[11px] text-zinc-300">
                      {finalResponse.evidence_refs.map((evidence) => (
                        <li key={evidence.evidence_id}>
                          <span className="font-mono">{evidence.source_id}</span>
                          {evidence.excerpt ? ` — ${evidence.excerpt}` : ''}
                        </li>
                      ))}
                      {finalResponse.evidence_refs.length === 0 && (
                        <li className="text-zinc-600">Không có evidence.</li>
                      )}
                    </ul>
                  </div>

                  {finalResponse.unresolved.length > 0 && (
                    <div>
                      <h4 className="text-[10px] uppercase tracking-wide text-zinc-600">
                        Unresolved
                      </h4>
                      <ul className="mt-1 space-y-1 text-zinc-300">
                        {finalResponse.unresolved.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-xs text-zinc-500">
                  Run đã kết thúc nhưng chưa có final response payload.
                </p>
              )}
            </section>
          )}
        </div>
      </section>
    </div>
  );
}
