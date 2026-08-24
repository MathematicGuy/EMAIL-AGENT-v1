import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ExecutionTraceDrawer } from './ExecutionTraceDrawer';
import type { ChatActivity } from '../types';

describe('ExecutionTraceDrawer', () => {
  it('renders reasoning and filenames in fallback mode when no activities are provided', () => {
    const onClose = vi.fn();
    render(
      <ExecutionTraceDrawer
        onClose={onClose}
        trace={{
          provider: 'mimo',
          model: 'mimo-v2.5-pro',
          mode: 'reasoning',
          reasoning: 'Compare the supplied values.',
          reasoningTruncated: false,
          retrievedFilenames: ['brief.pdf', 'notes.docx'],
        }}
      />,
    );

    expect(screen.getByText('Compare the supplied values.')).toBeTruthy();
    expect(screen.getByText('brief.pdf')).toBeTruthy();
    expect(screen.getByText('notes.docx')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Đóng chi tiết xử lý' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('renders per-step execution trace attributing server activities and provider reasoning', () => {
    const onClose = vi.fn();
    const activities: ChatActivity[] = [
      { code: 'understanding_request', status: 'completed' },
      { code: 'reviewing_context', status: 'completed' },
      {
        code: 'searching_relevant_information',
        status: 'completed',
        outcome: 'success',
        detail: { kind: 'documents_found', current: 2 },
      },
      { code: 'preparing_response', status: 'completed' },
    ];

    render(
      <ExecutionTraceDrawer
        onClose={onClose}
        activities={activities}
        trace={{
          provider: 'mistral',
          model: 'mistral-medium-3-5',
          mode: 'reasoning',
          reasoning: 'Step 1: Analyze problem constraints.\nStep 2: Derive final answer.',
          reasoningTruncated: false,
          retrievedFilenames: ['guidelines.md'],
        }}
      />,
    );

    // Verify per-step titles
    expect(screen.getByText('1. Hiểu yêu cầu')).toBeTruthy();
    expect(screen.getByText('2. Xem lại ngữ cảnh liên quan')).toBeTruthy();
    expect(screen.getByText('3. Tìm thông tin liên quan')).toBeTruthy();
    expect(screen.getByText('4. Tổng hợp câu trả lời')).toBeTruthy();

    // Verify badges distinguishing server from provider
    expect(screen.getAllByText('Hệ thống')).toHaveLength(3);
    expect(screen.getAllByText('Lập luận mô hình').length).toBeGreaterThanOrEqual(1);

    // Verify step 1 simplified routing info
    expect(screen.getByText('Phân tích yêu cầu:')).toBeTruthy();
    expect(screen.getByText('RAG')).toBeTruthy();

    // Verify retrieved filenames and reasoning
    expect(screen.getByText('guidelines.md')).toBeTruthy();
    expect(screen.getByText(/Step 1: Analyze problem constraints/)).toBeTruthy();
  });

  it('displays fast mode clarification when reasoning is disabled', () => {
    const activities: ChatActivity[] = [
      { code: 'understanding_request', status: 'completed' },
      { code: 'preparing_response', status: 'completed' },
    ];

    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activities={activities}
        trace={{
          provider: 'mimo',
          model: 'mimo-v2.5-pro',
          mode: 'fast',
          reasoning: undefined,
          reasoningTruncated: false,
          retrievedFilenames: [],
        }}
      />,
    );

    expect(screen.getByText(/Chế độ Nhanh:/)).toBeTruthy();
    expect(screen.getByText(/Thinking disabled/)).toBeTruthy();
  });

  it('displays waiting indicator when generation is in progress', () => {
    const activities: ChatActivity[] = [
      { code: 'understanding_request', status: 'completed' },
      { code: 'preparing_response', status: 'running' },
    ];

    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activities={activities}
        generationStatus="generating"
      />,
    );

    expect(screen.getByText('Đang thu thập thông tin xử lý...')).toBeTruthy();
    expect(screen.getByText('Đang chờ mô hình thực hiện suy luận...')).toBeTruthy();
  });
});
