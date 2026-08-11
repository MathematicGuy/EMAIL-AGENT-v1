import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(cleanup);
import { TaskWorkflowCard } from './TaskWorkflowCard';
import type { TaskWorkflow } from '../types';
import type { RunEvent, TaskDetail } from '../../modules/work-intake/types';

function workflow(overrides: Partial<TaskWorkflow> = {}): TaskWorkflow {
  return {
    taskId: 'task-1',
    detail: {
      task: { task_id: 'task-1', status: 'AWAITING_PLAN_APPROVAL', state_version: 3 },
      run: null,
      plan: {
        plan_version: 1,
        approved: false,
        objective: 'Tổng hợp báo cáo quý 2',
        steps: [
          { step_id: 'step_collect', capability_id: 'documents.collect', state: 'PENDING' },
          { step_id: 'step_synthesize', capability_id: 'documents.synthesize', state: 'PENDING' },
        ],
      },
      steps: [
        { step_id: 'step_collect', capability_id: 'documents.collect', state: 'PENDING' },
        { step_id: 'step_synthesize', capability_id: 'documents.synthesize', state: 'PENDING' },
      ],
    } as unknown as TaskDetail,
    events: [],
    phase: 'Đang chờ bạn phê duyệt plan',
    connectionState: 'live',
    lastEventSequence: 0,
    ...overrides,
  };
}

function failedWorkflow(retryable: boolean): TaskWorkflow {
  const base = workflow();
  const detail = base.detail as TaskDetail;
  return {
    ...base,
    detail: {
      ...detail,
      task: { ...detail.task, status: 'FAILED' },
      steps: [
        { ...detail.steps[0], state: 'SUCCEEDED' },
        { ...detail.steps[1], state: 'FAILED' },
      ],
    },
    events: [
      {
        aggregate_type: 'STEP',
        aggregate_id: 'step_synthesize',
        event_type: 'step.failed',
        sequence: 4,
        error: { retryable, user_message: 'Executor timeout.' },
      } as unknown as RunEvent,
    ],
    phase: 'Thất bại',
  };
}

describe('TaskWorkflowCard', () => {
  it('renders every plan step from the server plan', () => {
    render(<TaskWorkflowCard workflow={workflow()} />);
    expect(screen.getByText('Đang chờ bạn phê duyệt plan')).toBeTruthy();
    const disclosure = screen.getByText('Chi tiết các bước').closest('details');
    expect(disclosure?.open).toBe(false);
    expect(screen.getByText('step_collect')).toBeTruthy();
    expect(screen.getByText('step_synthesize')).toBeTruthy();
    fireEvent.click(screen.getByText('Chi tiết các bước'));
    expect(disclosure?.open).toBe(true);
  });

  it('approves then requests a revision with the typed feedback', async () => {
    const onApprove = vi.fn();
    const onRevise = vi.fn();
    render(
      <TaskWorkflowCard
        workflow={workflow()}
        onApprove={onApprove}
        onRevise={onRevise}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Phê duyệt plan' }));
    expect(onApprove).toHaveBeenCalledWith('task-1');
    await waitFor(() =>
      expect(screen.getByText('Plan đã được phê duyệt — đang thực hiện.')).toBeTruthy()
    );

    fireEvent.change(screen.getByLabelText('Góp ý cho plan'), {
      target: { value: 'Thêm bước kiểm tra số liệu' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Yêu cầu chỉnh sửa' }));
    await waitFor(() =>
      expect(onRevise).toHaveBeenCalledWith('task-1', 'Thêm bước kiểm tra số liệu')
    );
  });

  it('shows approval progress and locks plan controls while approval is pending', () => {
    const onApprove = vi.fn(
      () => new Promise<void>(() => undefined)
    );
    const onRevise = vi.fn();
    render(
      <TaskWorkflowCard
        workflow={workflow()}
        onApprove={onApprove}
        onRevise={onRevise}
      />
    );

    fireEvent.change(screen.getByLabelText('Góp ý cho plan'), {
      target: { value: 'Thêm bước kiểm tra số liệu' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Phê duyệt plan' }));

    expect(screen.getByText('Đang phê duyệt plan…')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Phê duyệt plan' }) as HTMLButtonElement)
        .disabled
    ).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Yêu cầu chỉnh sửa' }) as HTMLButtonElement)
        .disabled
    ).toBe(true);
  });

  it('shows an approval error and restores plan controls after a failed request', async () => {
    const onApprove = vi.fn().mockRejectedValue(new Error('Backend trả về lỗi.'));
    render(<TaskWorkflowCard workflow={workflow()} onApprove={onApprove} />);

    fireEvent.click(screen.getByRole('button', { name: 'Phê duyệt plan' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Backend trả về lỗi.');
    expect(
      (screen.getByRole('button', { name: 'Phê duyệt plan' }) as HTMLButtonElement)
        .disabled
    ).toBe(false);
    expect(screen.getByText('Đang chờ bạn phê duyệt plan')).toBeTruthy();
  });

  it('offers a retry only for a retryable failed step', () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <TaskWorkflowCard workflow={failedWorkflow(true)} onRetry={onRetry} />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Thử lại' }));
    expect(onRetry).toHaveBeenCalledWith('task-1', 'step_synthesize');

    rerender(<TaskWorkflowCard workflow={failedWorkflow(false)} onRetry={onRetry} />);
    expect(screen.queryByRole('button', { name: 'Thử lại' })).toBeNull();
  });

  it('says the progress is stale when the backend is unreachable', () => {
    render(
      <TaskWorkflowCard workflow={workflow({ connectionState: 'unavailable' })} />
    );
    expect(
      screen.getByText('Không thể cập nhật tiến độ từ backend.')
    ).toBeTruthy();
  });
});
