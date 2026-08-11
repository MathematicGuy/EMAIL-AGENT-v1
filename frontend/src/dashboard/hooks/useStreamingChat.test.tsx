import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStreamingChat, validateAttachmentFile } from './useStreamingChat';
import {
  cancelTurn,
  sendConversationMessage,
  streamTurnEvents,
} from '../../modules/work-intake/assistantApi';
import { approvePlan, getTask, listTaskEvents } from '../../modules/work-intake/api';
import type { TurnEvent } from '../../modules/work-intake/assistantApi';
import type { TaskDetail } from '../../modules/work-intake/types';

vi.mock('../../modules/work-intake/assistantApi', async () => {
  const actual = await vi.importActual<
    typeof import('../../modules/work-intake/assistantApi')
  >('../../modules/work-intake/assistantApi');
  return {
    ...actual,
    checkAssistantApi: vi.fn().mockResolvedValue(true),
    createConversation: vi.fn().mockResolvedValue('conv-1'),
    listConversations: vi.fn().mockResolvedValue([]),
    listConversationMessages: vi.fn().mockResolvedValue([]),
    sendConversationMessage: vi.fn(),
    streamTurnEvents: vi.fn(),
    cancelTurn: vi.fn(),
  };
});

vi.mock('../../modules/work-intake/api', () => ({
  getTask: vi.fn(),
  listTaskEvents: vi.fn(),
  approvePlan: vi.fn(),
  revisePlan: vi.fn(),
  retryStep: vi.fn(),
}));

function event(id: number, name: string, data: Record<string, unknown>): TurnEvent {
  return { id, event: name, data };
}

function streamEvents(events: TurnEvent[]): void {
  vi.mocked(streamTurnEvents).mockImplementation(async (input) => {
    for (const item of events) input.onEvent(item);
  });
}

function detail(overrides: Partial<TaskDetail['task']> = {}): TaskDetail {
  return {
    task: {
      task_id: 'task-1',
      status: 'AWAITING_PLAN_APPROVAL',
      state_version: 3,
      ...overrides,
    },
    run: null,
    plan: {
      plan_version: 1,
      approved: false,
      query_template_hash: 'hash-1',
      approval_mode: 'REQUIRE_INTERACTIVE',
      steps: [{ step_id: 'step_synthesize' }],
    },
    steps: [{ step_id: 'step_synthesize', state: 'PENDING' }],
  } as unknown as TaskDetail;
}

beforeEach(() => {
  vi.mocked(sendConversationMessage).mockResolvedValue({
    conversation_id: 'conv-1',
    turn_id: 'turn-1',
    events_url: '/events',
  });
  streamEvents([]);
  vi.mocked(getTask).mockResolvedValue(detail());
  vi.mocked(listTaskEvents).mockResolvedValue({
    events: [],
    next_sequence: 0,
    has_more: false,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('useStreamingChat task workflow state', () => {
  it('sends the model selected by the dashboard', async () => {
    const { result } = renderHook(() => useStreamingChat('deepseek-nvidia'));

    await act(async () => {
      await result.current.sendMessage('Xin chào');
    });

    expect(sendConversationMessage).toHaveBeenCalledWith(
      expect.objectContaining({ modelId: 'deepseek-nvidia' })
    );
  });

  it('resumes the clarification turn with the next message', async () => {
    streamEvents([
      event(1, 'assistant.delta', { delta: 'Kỳ nào?' }),
      event(2, 'clarification.requested', {
        question: 'Kỳ nào?',
        quick_actions: ['Tóm tắt nội dung tài liệu'],
      }),
    ]);
    const { result } = renderHook(() => useStreamingChat());

    await act(async () => {
      await result.current.sendMessage('Tạo báo cáo');
    });
    expect(result.current.pendingClarificationTurnId).toBe('turn-1');
    expect(result.current.messages.at(-1)?.quickActions).toEqual([
      'Tóm tắt nội dung tài liệu',
    ]);

    streamEvents([]);
    await act(async () => {
      await result.current.sendMessage('Quý 2');
    });

    expect(sendConversationMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ replyToTurnId: 'turn-1' })
    );
    expect(streamTurnEvents).toHaveBeenLastCalledWith(
      expect.objectContaining({ turnId: 'turn-1', afterSequence: 2 })
    );
    expect(result.current.pendingClarificationTurnId).toBeNull();
  });

  it('releases generation while the clarification stream remains open', async () => {
    let releaseStream: (() => void) | undefined;
    vi.mocked(streamTurnEvents).mockImplementation(async (input) => {
      input.onEvent(
        event(1, 'clarification.requested', {
          question: 'Bạn muốn tạo lịch cho kỳ World Cup nào?',
          quick_actions: ['World Cup 2025'],
        })
      );
      await new Promise<void>((resolve) => {
        releaseStream = resolve;
      });
    });
    const { result } = renderHook(() => useStreamingChat());
    let sendPromise: Promise<void> | undefined;

    act(() => {
      sendPromise = result.current.sendMessage(
        'Help me to create a schedule for the 2025 world cup'
      );
    });

    await waitFor(() => {
      expect(result.current.pendingClarificationTurnId).toBe('turn-1');
      expect(result.current.isGenerating).toBe(false);
    });

    expect(releaseStream).toBeDefined();
    await act(async () => {
      releaseStream?.();
      await sendPromise;
    });
  });

  it('stops only the local stream when clarification is requested', async () => {
    vi.mocked(streamTurnEvents).mockImplementation(
      (input) =>
        new Promise<void>((_resolve, reject) => {
          input.signal?.addEventListener(
            'abort',
            () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })),
            { once: true }
          );
          input.onEvent(
            event(2, 'clarification.requested', {
              question: 'Học kỳ nào?',
              quick_actions: ['Học kỳ mùa Thu', 'Học kỳ mùa Xuân'],
            })
          );
        })
    );
    const { result } = renderHook(() => useStreamingChat());

    await act(async () => {
      await result.current.sendMessage('Giúp tôi tạo lịch đi học Vin University');
    });

    expect(result.current.isGenerating).toBe(false);
    expect(result.current.pendingClarificationTurnId).toBe('turn-1');
    expect(result.current.messages.at(-1)).toMatchObject({
      isStreaming: false,
      quickActions: ['Học kỳ mùa Thu', 'Học kỳ mùa Xuân'],
    });
    expect(cancelTurn).not.toHaveBeenCalled();
  });

  it('resets the stream cursor when a clarification reply becomes a new turn', async () => {
    streamEvents([
      event(1, 'assistant.delta', { delta: 'Bạn muốn xem trận nào?' }),
      event(2, 'clarification.requested', {
        question: 'Bạn muốn xem trận nào?',
        quick_actions: ['Trận mở màn', 'Chung kết'],
      }),
    ]);
    const { result } = renderHook(() => useStreamingChat());

    await act(async () => {
      await result.current.sendMessage('Tôi muốn lập lịch xem World Cup');
    });

    vi.mocked(sendConversationMessage).mockResolvedValueOnce({
      conversation_id: 'conv-1',
      turn_id: 'turn-2',
      events_url: '/events',
    });
    streamEvents([]);
    await act(async () => {
      await result.current.sendMessage('Lịch học tại VinUni');
    });

    expect(sendConversationMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ replyToTurnId: 'turn-1' })
    );
    expect(streamTurnEvents).toHaveBeenLastCalledWith(
      expect.objectContaining({ turnId: 'turn-2', afterSequence: 0 })
    );
  });

  it('retries an existing failed assistant turn in-place without creating a new user message', async () => {
    const { result } = renderHook(() => useStreamingChat());

    streamEvents([
      event(1, 'assistant.failed', {
        user_message: 'Assistant Runtime không hoàn thành được lượt này.',
      }),
    ]);
    await act(async () => {
      await result.current.sendMessage('Tìm kiếm ngay');
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0]).toMatchObject({
      role: 'user',
      content: 'Tìm kiếm ngay',
    });
    expect(result.current.messages[1]).toMatchObject({
      role: 'assistant',
      content: '**Backend trả về lỗi**\n\nAssistant Runtime không hoàn thành được lượt này.',
    });

    const failedAssistantId = result.current.messages[1].id;

    streamEvents([
      event(1, 'assistant.delta', { delta: 'Kết quả tìm kiếm' }),
      event(2, 'assistant.completed', {}),
    ]);
    await act(async () => {
      await result.current.retryTurn(failedAssistantId);
    });

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0]).toMatchObject({
      role: 'user',
      content: 'Tìm kiếm ngay',
    });
    expect(result.current.messages[1]).toMatchObject({
      id: failedAssistantId,
      role: 'assistant',
      content: 'Kết quả tìm kiếm',
      isStreaming: false,
    });
  });

  it('loads the authoritative task detail when the task is created', async () => {
    streamEvents([event(1, 'task.created', { task_id: 'task-1' })]);
    const { result } = renderHook(() => useStreamingChat());

    await act(async () => {
      await result.current.sendMessage('Tạo báo cáo quý 2');
    });

    await waitFor(() =>
      expect(result.current.workflows['task-1']?.detail?.task.task_id).toBe('task-1')
    );
    const workflow = result.current.workflows['task-1'];
    expect(workflow.phase).toBe('Đang chờ bạn phê duyệt plan');
    expect(workflow.connectionState).toBe('live');
  });

  it('keeps the last confirmed state when the backend stops answering', async () => {
    streamEvents([event(1, 'task.created', { task_id: 'task-1' })]);
    const { result } = renderHook(() => useStreamingChat());
    await act(async () => {
      await result.current.sendMessage('Tạo báo cáo quý 2');
    });
    await waitFor(() => expect(result.current.workflows['task-1']?.detail).toBeTruthy());

    vi.mocked(getTask).mockRejectedValue(new Error('offline'));
    vi.mocked(listTaskEvents).mockRejectedValue(new Error('offline'));
    await act(async () => {
      await result.current.refreshWorkflow('task-1');
    });

    const workflow = result.current.workflows['task-1'];
    expect(workflow.connectionState).toBe('unavailable');
    expect(workflow.detail?.task.status).toBe('AWAITING_PLAN_APPROVAL');
  });

  it('approves the current plan version and refreshes from the server', async () => {
    streamEvents([event(1, 'task.created', { task_id: 'task-1' })]);
    const { result } = renderHook(() => useStreamingChat());
    await act(async () => {
      await result.current.sendMessage('Tạo báo cáo quý 2');
    });
    await waitFor(() => expect(result.current.workflows['task-1']?.detail).toBeTruthy());

    vi.mocked(getTask).mockResolvedValue(detail({ status: 'RUNNING' }));
    await act(async () => {
      await result.current.approveWorkflowPlan('task-1');
    });

    expect(approvePlan).toHaveBeenCalledWith(
      expect.objectContaining({
        taskId: 'task-1',
        planVersion: 1,
        queryTemplateHash: 'hash-1',
      })
    );
    await waitFor(() =>
      expect(result.current.workflows['task-1'].phase).toBe(
        'Đang thực hiện yêu cầu…'
      )
    );
  });
});

describe('chat attachment format policy', () => {
  it.each([
    ['contract.pdf', 'application/pdf'],
    [
      'meeting.docx',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    ],
    ['notes.md', 'text/markdown'],
  ])('accepts %s', (name, type) => {
    expect(validateAttachmentFile(new File(['content'], name, { type }))).toBeNull();
  });

  it.each([
    ['sales.csv', 'text/csv'],
    [
      'sales.xlsx',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    ],
    ['page.html', 'text/html'],
  ])('rejects %s', (name, type) => {
    expect(validateAttachmentFile(new File(['content'], name, { type }))).toContain(
      'PDF, DOCX hoặc Markdown'
    );
  });
});

