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
    expect(screen.getByText('Episodic (Ký ức tình tiết & Tác vụ)')).toBeTruthy();

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
    expect(screen.getByText('Một phần suy giảm')).toBeTruthy();
  });

  it('shows the RAG route with chunk count, document pills and reasoning duration', () => {
    const activities: ChatActivity[] = [
      {
        code: 'understanding_request',
        status: 'completed',
        startedAt: '2026-08-24T10:00:00.000Z',
        completedAt: '2026-08-24T10:00:02.000Z',
      },
      {
        code: 'searching_relevant_information',
        status: 'completed',
        outcome: 'success',
        detail: { kind: 'documents_found', current: 4 },
        startedAt: '2026-08-24T10:00:02.000Z',
        completedAt: '2026-08-24T10:00:03.200Z',
      },
      {
        code: 'preparing_response',
        status: 'completed',
        startedAt: '2026-08-24T10:00:03.200Z',
        completedAt: '2026-08-24T10:00:08.100Z',
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
    expect(screen.getByText('2s')).toBeTruthy();
    expect(screen.getByText('1,2s')).toBeTruthy();
    expect(screen.getByText('4,9s')).toBeTruthy();
    expect(screen.getByText(/Truy vấn trong 1,2 giây/)).toBeTruthy();
    expect(screen.getByText('Suy luận trong 4,9 giây')).toBeTruthy();
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

  it('displays the 4 memory tiers, project scope, and anti-leak guard in the memory tab', () => {
    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activities={[{ code: 'reviewing_context', status: 'completed', outcome: 'success' }]}
        activeProjectName="Finance Operations"
        sessionTurnCount={5}
      />,
    );

    fireEvent.click(screen.getByRole('tab', { name: /Bộ nhớ/ }));
    expect(screen.getByText('Short-Term (Session Buffer)')).toBeTruthy();
    expect(screen.getByText('Long-Term (Hồ sơ & Sở thích)')).toBeTruthy();
    expect(screen.getByText('Episodic (Ký ức tình tiết & Tác vụ)')).toBeTruthy();
    expect(screen.getByText('Semantic (Tri thức & RAG)')).toBeTruthy();
    expect(screen.getByText('Finance Operations')).toBeTruthy();
    expect(screen.getByText('5 lượt')).toBeTruthy();
    expect(screen.getByText('Cơ chế cách ly bộ nhớ (Anti-Leak Guard)')).toBeTruthy();
  });

  it('renders detailed RAG evidence with search and expands content preview', () => {
    const ragEvidence = [
      {
        source: 'company_knowledge' as const,
        retrievalStatus: 'success' as const,
        chunkId: 'chunk-001',
        documentId: 'doc-hr',
        documentTitle: 'Chính sách nghỉ phép 2026',
        section: 'Điều 5. Nghỉ phép năm',
        sourceUrl: 'https://internal.wiki/leave',
        relevanceScore: 0.942,
        rerankScore: 0.965,
        preview: 'Nhân viên có 12 ngày phép năm tiêu chuẩn...',
        content: 'Nhân viên có 12 ngày phép năm tiêu chuẩn. Sau 5 năm làm việc được cộng thêm 1 ngày...',
      },
    ];

    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activities={[
          {
            code: 'searching_relevant_information',
            status: 'completed',
            detail: { kind: 'documents_found', current: 1 },
          },
        ]}
        message={{
          id: 'msg-1',
          role: 'assistant',
          content: 'Bạn có 12 ngày phép.',
          timestamp: '2026-08-25T08:00:00Z',
          ragEvidence,
        }}
      />,
    );

    fireEvent.click(screen.getByRole('tab', { name: /Bộ nhớ/ }));

    expect(screen.getByText('Chính sách nghỉ phép 2026')).toBeTruthy();
    expect(screen.getByText('Điều 5. Nghỉ phép năm')).toBeTruthy();
    expect(screen.getByText('94.2% khớp')).toBeTruthy();
    expect(screen.getByText('Rerank: 97%')).toBeTruthy();
    expect(screen.getByText('Nhân viên có 12 ngày phép năm tiêu chuẩn...')).toBeTruthy();

    // Expand chunk content
    const expandBtn = screen.getByRole('button', { name: /Xem đầy đủ/ });
    fireEvent.click(expandBtn);
    expect(
      screen.getByText(
        'Nhân viên có 12 ngày phép năm tiêu chuẩn. Sau 5 năm làm việc được cộng thêm 1 ngày...',
      ),
    ).toBeTruthy();

    // Search filter
    const searchInput = screen.getByPlaceholderText('Lọc đoạn trích dẫn tri thức...');
    fireEvent.change(searchInput, { target: { value: 'nghỉ phép' } });
    expect(screen.getByText('Chính sách nghỉ phép 2026')).toBeTruthy();

    fireEvent.change(searchInput, { target: { value: 'không tồn tại' } });
    expect(screen.queryByText('Chính sách nghỉ phép 2026')).toBeNull();
  });

  it('copies full memory context snapshot to clipboard', () => {
    const writeText = vi.fn();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });

    render(
      <ExecutionTraceDrawer
        onClose={vi.fn()}
        activeProjectName="Marketing Campaign"
        sessionTurnCount={3}
      />,
    );

    fireEvent.click(screen.getByRole('tab', { name: /Bộ nhớ/ }));

    const copyBtn = screen.getByRole('button', { name: 'Sao chép bối cảnh bộ nhớ' });
    fireEvent.click(copyBtn);

    expect(writeText).toHaveBeenCalledOnce();
    const calledArg = JSON.parse(writeText.mock.calls[0][0]);
    expect(calledArg.project).toBe('Marketing Campaign');
    expect(calledArg.session_turn_count).toBe(3);
    expect(calledArg.memory_tiers.short_term.status).toBe('active');
    expect(calledArg.isolation_guard.fail_closed).toBe(true);
  });
});
