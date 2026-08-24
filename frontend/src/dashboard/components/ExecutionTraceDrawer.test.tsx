import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ExecutionTraceDrawer } from './ExecutionTraceDrawer';
import type { ChatActivity } from '../types';

afterEach(() => {
  cleanup();
});

describe('ExecutionTraceDrawer', () => {
  it('renders the 3-step timeline and closes on request', () => {
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

    expect(screen.getByText('1. Hiểu yêu cầu')).toBeTruthy();
    expect(screen.getByText('2. Tìm thông tin liên quan')).toBeTruthy();
    expect(screen.getByText('3. Tổng hợp câu trả lời')).toBeTruthy();
    expect(screen.getByText('Compare the supplied values.')).toBeTruthy();
    expect(screen.getByText('brief.pdf')).toBeTruthy();
    expect(screen.getByText('notes.docx')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Đóng chi tiết xử lý' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('switches between the process and memory tabs', () => {
    const activities: ChatActivity[] = [
      { code: 'understanding_request', status: 'completed' },
      { code: 'reviewing_context', status: 'completed', outcome: 'success' },
      { code: 'preparing_response', status: 'completed' },
    ];

    render(<ExecutionTraceDrawer onClose={vi.fn()} activities={activities} />);

    const processTab = screen.getByRole('tab', { name: /Tiến trình xử lý/ });
    const memoryTab = screen.getByRole('tab', { name: /Bộ nhớ/ });
    expect(processTab.getAttribute('aria-selected')).toBe('true');
    expect(memoryTab.getAttribute('aria-selected')).toBe('false');

    fireEvent.click(memoryTab);

    expect(memoryTab.getAttribute('aria-selected')).toBe('true');
    expect(screen.queryByText('1. Hiểu yêu cầu')).toBeNull();
    expect(screen.getByText('Bộ nhớ tình tiết (Episodic):')).toBeTruthy();
    expect(screen.getAllByText('Sẵn sàng & Đồng bộ')).toHaveLength(2);

    fireEvent.click(processTab);
    expect(screen.getByText('1. Hiểu yêu cầu')).toBeTruthy();
  });

  it('reports degraded memory in the memory tab', () => {
    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activities={[{ code: 'reviewing_context', status: 'completed', outcome: 'degraded' }]}
      />,
    );

    fireEvent.click(screen.getByRole('tab', { name: /Bộ nhớ/ }));
    expect(screen.getAllByText('Một phần suy giảm')).toHaveLength(2);
  });

  it('shows the RAG route with chunk count, document pills and reasoning duration', () => {
    const activities: ChatActivity[] = [
      { code: 'understanding_request', status: 'completed' },
      {
        code: 'searching_relevant_information',
        status: 'completed',
        outcome: 'success',
        detail: { kind: 'documents_found', current: 4 },
      },
      {
        code: 'preparing_response',
        status: 'completed',
        startedAt: '2026-08-24T10:00:00.000Z',
        completedAt: '2026-08-24T10:00:16.800Z',
      },
    ];

    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
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

    expect(screen.getByText('RAG · Truy xuất tài liệu')).toBeTruthy();
    expect(screen.getByText(/đoạn nội dung liên quan/).textContent).toContain('4');
    expect(screen.getByText('guidelines.md')).toBeTruthy();
    expect(screen.getByText('mistral-medium-3-5')).toBeTruthy();
    expect(screen.getByText('Suy luận trong 16,8 giây')).toBeTruthy();
    expect(screen.getByText(/Step 1: Analyze problem constraints/)).toBeTruthy();
  });

  it('states that no retrieval was needed for a direct chat turn', () => {
    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activities={[
          { code: 'understanding_request', status: 'completed' },
          { code: 'preparing_response', status: 'completed' },
        ]}
        trace={{
          provider: 'mimo',
          model: 'mimo-v2.5-pro',
          mode: 'reasoning',
          reasoning: 'Direct answer.',
          reasoningTruncated: false,
          retrievedFilenames: [],
        }}
      />,
    );

    expect(screen.getByText('Direct · Hội thoại trực tiếp')).toBeTruthy();
    expect(screen.getByText('Không yêu cầu truy xuất tài liệu')).toBeTruthy();
  });

  it('copies the chain of thought from the synthesis step', () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });

    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        trace={{
          provider: 'mimo',
          model: 'mimo-v2.5-pro',
          mode: 'reasoning',
          reasoning: 'Chuỗi suy luận đầy đủ.',
          reasoningTruncated: false,
          retrievedFilenames: [],
        }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Sao chép chuỗi suy luận' }));
    expect(writeText).toHaveBeenCalledWith('Chuỗi suy luận đầy đủ.');
    expect(screen.getByText('Đã sao chép!')).toBeTruthy();
  });

  it('displays fast mode clarification when reasoning is disabled', () => {
    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activities={[
          { code: 'understanding_request', status: 'completed' },
          { code: 'preparing_response', status: 'completed' },
        ]}
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
    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activities={[
          { code: 'understanding_request', status: 'completed' },
          { code: 'preparing_response', status: 'running' },
        ]}
        generationStatus="generating"
      />,
    );

    expect(screen.getByText('Đang thu thập thông tin xử lý...')).toBeTruthy();
    expect(screen.getByText('Đang chờ mô hình thực hiện suy luận...')).toBeTruthy();
  });
});
