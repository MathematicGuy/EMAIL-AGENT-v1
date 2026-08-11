import React, { useMemo, useState } from 'react';
import {
  AlertCircle,
  BadgeCheck,
  FileCheck2,
  Play,
  ShieldCheck,
  UploadCloud
} from 'lucide-react';
import { readResourceText, uploadResource } from '../../workspace/resourceApi';
import {
  approvePlan,
  getTask,
  submitQuery,
  type WorkIntakeScope
} from '../../work-intake/api';
import {
  isTerminalStatus,
  type TaskDetail
} from '../../work-intake/types';
import type { AgentResult } from '../types';
import {
  DOCUMENT_PRESETS,
  type DocumentPreset
} from '../presets';
import { ProgressBar } from './ProgressBar';
import { ResultPanel } from './ResultPanel';

type Phase =
  | 'idle'
  | 'uploading'
  | 'planning'
  | 'awaiting_approval'
  | 'running'
  | 'done'
  | 'error';

const SCOPE: WorkIntakeScope = {
  actorId: 'demo-user',
  projectId: 'demo-project',
  workspaceId: 'demo-workspace'
};

const POLL_INTERVAL_MS = 400;
const MAX_POLL_ATTEMPTS = 150;

function phaseLabel(phase: Phase): string {
  return {
    idle: 'Sẵn sàng',
    uploading: 'Đang lưu nguồn vào Resource Broker',
    planning: 'Module 1 đang lập kế hoạch',
    awaiting_approval: 'Chờ phê duyệt kế hoạch',
    running: 'Module 2 → Module 3 đang thực thi',
    done: 'Hoàn tất',
    error: 'Có lỗi'
  }[phase];
}

function progressFor(detail: TaskDetail | null, phase: Phase): number {
  if (phase === 'uploading') return 10;
  if (phase === 'planning') return 20;
  if (phase === 'awaiting_approval') return 30;
  if (!detail?.steps.length) return phase === 'done' ? 100 : 0;
  const finished = detail.steps.filter((step) =>
    ['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELLED', 'SKIPPED'].includes(step.state)
  ).length;
  return Math.max(35, Math.round((finished / detail.steps.length) * 100));
}

function moduleTwoResults(detail: TaskDetail | null): AgentResult[] {
  if (!detail?.step_results) return [];
  return detail.step_results.flatMap((item) => {
    try {
      const result = JSON.parse(item.result_json) as AgentResult & {
        producer?: string;
      };
      return result.producer === 'module_2' ? [result] : [];
    } catch {
      return [];
    }
  });
}

async function waitForTerminal(taskId: string): Promise<TaskDetail> {
  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt += 1) {
    const detail = await getTask({ taskId, scope: SCOPE });
    if (isTerminalStatus(detail.task.status)) return detail;
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
  throw new Error('Workflow chạy quá lâu và đã dừng polling.');
}

export const LiveUploadPanel: React.FC = () => {
  const [presetId, setPresetId] = useState(DOCUMENT_PRESETS[0].id);
  const preset = useMemo(
    () => DOCUMENT_PRESETS.find((item) => item.id === presetId) ?? DOCUMENT_PRESETS[0],
    [presetId]
  );
  const [files, setFiles] = useState<File[]>([]);
  const [objective, setObjective] = useState(preset.objective);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [artifact, setArtifact] = useState<string | null>(null);

  const choosePreset = (next: DocumentPreset) => {
    setPresetId(next.id);
    setFiles([]);
    setObjective(next.objective);
    setPhase('idle');
    setError(null);
    setDetail(null);
    setArtifact(null);
  };

  const cardinalityValid = files.length === preset.fileCount;
  const isBusy = ['uploading', 'planning', 'running'].includes(phase);
  const results = moduleTwoResults(detail);

  const loadArtifact = async (completed: TaskDetail) => {
    const refs = completed.final_response?.artifact_refs ?? [];
    const latest = refs.at(-1);
    if (!latest) return;
    setArtifact(await readResourceText(latest.ref_id, SCOPE));
  };

  const applyTerminalTask = async (completed: TaskDetail) => {
    setDetail(completed);
    if (completed.task.status === 'FAILED' || completed.task.status === 'CANCELLED') {
      setError(
        completed.final_response?.error?.user_message ??
          `Workflow ended with status ${completed.task.status}.`
      );
      setPhase('error');
      return;
    }
    await loadArtifact(completed);
    setPhase('done');
  };

  const handleRun = async () => {
    if (!cardinalityValid) {
      setError(`Preset ${preset.title} cần đúng ${preset.fileCount} file.`);
      return;
    }
    setError(null);
    setDetail(null);
    setArtifact(null);
    try {
      setPhase('uploading');
      const refs = await Promise.all(files.map((file) => uploadResource(file, SCOPE)));
      setPhase('planning');
      const task = await submitQuery({
        query: objective.trim(),
        scope: SCOPE,
        attachmentRefs: refs,
        outputPreference: { format: 'markdown', preset_id: preset.id }
      });
      const planned = await getTask({ taskId: task.task_id, scope: SCOPE });
      setDetail(planned);
      if (planned.task.status === 'AWAITING_PLAN_APPROVAL') {
        setPhase('awaiting_approval');
        return;
      }
      setPhase('running');
      const completed = await waitForTerminal(task.task_id);
      await applyTerminalTask(completed);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setPhase('error');
    }
  };

  const handleApprove = async () => {
    if (!detail?.plan?.query_template_hash) {
      setError('Kế hoạch thiếu query_template_hash nên không thể phê duyệt.');
      setPhase('error');
      return;
    }
    try {
      setError(null);
      setPhase('running');
      await approvePlan({
        taskId: detail.task.task_id,
        planVersion: detail.plan.plan_version,
        queryTemplateHash: detail.plan.query_template_hash,
        approvalMode: detail.plan.approval_mode ?? 'REQUIRE_INTERACTIVE',
        scope: SCOPE
      });
      const completed = await waitForTerminal(detail.task.task_id);
      await applyTerminalTask(completed);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setPhase('error');
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside>
        <p className="mb-2 text-xs font-medium uppercase tracking-[0.16em] text-[#6c6862]">
          1 · Chọn workflow
        </p>
        <div className="space-y-2">
          {DOCUMENT_PRESETS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => choosePreset(item)}
              aria-pressed={item.id === preset.id}
              className={`w-full rounded-xl border p-3 text-left transition-colors ${
                item.id === preset.id
                  ? 'border-[#d97757]/60 bg-[#d97757]/10'
                  : 'border-[#33312e] bg-[#22211e] hover:bg-[#292825]'
              }`}
            >
              <span className="text-sm font-medium text-[#f3f2ef]">{item.title}</span>
              <span className="mt-1 block text-xs leading-5 text-[#949089]">
                {item.description}
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="min-w-0 space-y-4">
        <div className="rounded-2xl border border-[#33312e] bg-[#292825] p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.16em] text-[#6c6862]">
                2 · Nguồn &amp; mục tiêu
              </p>
              <h2 className="mt-1 text-xl font-semibold text-[#f3f2ef]">{preset.title}</h2>
              <p className="mt-1 text-sm text-[#949089]">{preset.expectedOutcome}</p>
            </div>
            <span className="rounded-full border border-[#33312e] bg-[#22211e] px-3 py-1 text-xs text-[#d97757]">
              Broker → M1 → M2 → M3
            </span>
          </div>

          <label
            htmlFor="documents-preset-files"
            className="mt-5 flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-[#4a4742] bg-[#22211e] px-4 py-4 text-sm text-[#949089] transition-colors hover:border-[#d97757]/60"
          >
            <UploadCloud className="h-5 w-5 shrink-0 text-[#d97757]" />
            <span className="min-w-0">
              <span className="block truncate text-[#f3f2ef]">
                {files.length
                  ? files.map((file) => file.name).join(' · ')
                  : `Chọn ${preset.fileCount} file — ${preset.fileHint}`}
              </span>
              <span className="mt-0.5 block text-xs">
                Đã chọn {files.length}/{preset.fileCount}
              </span>
            </span>
            <input
              id="documents-preset-files"
              aria-label="Chọn file cho preset"
              type="file"
              accept={preset.accept}
              multiple={preset.fileCount > 1}
              className="sr-only"
              onChange={(event) =>
                setFiles(event.target.files ? Array.from(event.target.files) : [])
              }
            />
          </label>

          {!cardinalityValid && (
            <p className="mt-2 text-xs text-amber-300">
              Preset này cần đúng {preset.fileCount} file trước khi chạy.
            </p>
          )}

          <label className="mt-4 block text-xs font-medium text-[#949089]">
            Mục tiêu gửi Module 1
            <textarea
              aria-label="Mục tiêu workflow"
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              rows={4}
              className="mt-2 w-full rounded-xl border border-[#33312e] bg-[#1b1a17] p-3 text-sm leading-6 text-[#f3f2ef] focus:border-[#d97757]/60 focus:outline-none"
            />
          </label>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={handleRun}
              disabled={isBusy || !cardinalityValid || !objective.trim()}
              className="flex items-center gap-2 rounded-lg bg-[#d97757] px-4 py-2 text-sm font-medium text-[#1c1b18] transition-colors hover:bg-[#e08862] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#d97757] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Play className="h-4 w-4" />
              {isBusy ? phaseLabel(phase) : 'Tạo kế hoạch'}
            </button>
            {phase === 'awaiting_approval' && (
              <button
                type="button"
                onClick={handleApprove}
                className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-2 text-sm font-medium text-emerald-300"
              >
                <ShieldCheck className="h-4 w-4" />
                Phê duyệt &amp; chạy
              </button>
            )}
            <span className="text-xs text-[#6c6862]">{phaseLabel(phase)}</span>
          </div>

          {phase !== 'idle' && phase !== 'error' && (
            <div className="mt-4">
              <ProgressBar
                percent={progressFor(detail, phase)}
                label={phaseLabel(phase)}
              />
            </div>
          )}
          {detail?.plan && (
            <div className="mt-4 rounded-xl border border-[#33312e] bg-[#22211e] p-3">
              <div className="flex items-center gap-2 text-xs text-[#f3f2ef]">
                <BadgeCheck className="h-4 w-4 text-[#d97757]" />
                Plan v{detail.plan.plan_version} · {detail.plan.steps.length} bước
              </div>
              <p className="mt-2 text-xs leading-5 text-[#949089]">
                {detail.plan.steps
                  .map((step) => `${step.assigned_module}: ${step.capability_id}`)
                  .join(' → ')}
              </p>
            </div>
          )}
          {error && (
            <div
              role="alert"
              className="mt-4 flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300"
            >
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {results.length > 0 && (
          <div className="rounded-2xl border border-[#33312e] bg-[#292825] p-5">
            <div className="mb-4 flex items-center gap-2">
              <FileCheck2 className="h-4 w-4 text-emerald-400" />
              <h3 className="text-sm font-medium text-[#f3f2ef]">
                {results.length} kết quả Module 2
              </h3>
            </div>
            <div className="space-y-5">
              {results.map((result) => (
                <article
                  key={result.job_id}
                  className="rounded-xl border border-[#33312e] bg-[#22211e] p-4"
                >
                  <ResultPanel result={result} />
                </article>
              ))}
            </div>
          </div>
        )}

        {artifact && (
          <div className="rounded-2xl border border-[#33312e] bg-[#292825] p-5">
            <div className="mb-3 flex items-center gap-2">
              <BadgeCheck className="h-4 w-4 text-emerald-400" />
              <h3 className="text-sm font-medium text-[#f3f2ef]">
                Artifact từ Resource Broker
              </h3>
            </div>
            <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap rounded-xl bg-[#1b1a17] p-4 text-sm leading-6 text-[#d8d5cf]">
              {artifact}
            </pre>
          </div>
        )}
      </section>
    </div>
  );
};
