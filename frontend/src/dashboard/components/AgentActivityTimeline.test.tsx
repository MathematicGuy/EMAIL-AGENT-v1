import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChatActivity } from '../types';
import { AgentActivityTimeline } from './AgentActivityTimeline';

const activities: ChatActivity[] = [
  {
    code: 'understanding_request', status: 'completed', outcome: 'success',
    startedAt: '2026-08-24T00:00:00Z', completedAt: '2026-08-24T00:00:01Z',
  },
  {
    code: 'searching_relevant_information', status: 'running',
    startedAt: '2026-08-24T00:00:01Z', detail: { kind: 'documents_found', current: 3, total: 3 },
  },
  { code: 'preparing_response', status: 'pending' },
];

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('AgentActivityTimeline', () => {
  it('shows Vietnamese user-centric steps with only one running animation', () => {
    const { container } = render(
      <AgentActivityTimeline activities={activities} generationStatus="generating" />
    );
    expect(screen.getByText('Đang làm việc')).not.toBeNull();
    expect(screen.getByText('Hiểu yêu cầu')).not.toBeNull();
    expect(screen.getByText('Tìm thông tin liên quan')).not.toBeNull();
    expect(screen.getByText('└─ Tìm thấy 3 tài liệu liên quan')).not.toBeNull();
    expect(container.querySelectorAll('.animate-spin')).toHaveLength(1);
    expect(container.textContent).not.toMatch(/router|retriever|gemini|guardrail/i);
  });

  it('collapses a just-completed live timeline after 800 ms and can reopen it', () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <AgentActivityTimeline activities={activities} generationStatus="generating" />
    );
    const done = activities.map((item) => ({
      ...item, status: 'completed' as const, completedAt: '2026-08-24T00:00:02Z',
    }));
    rerender(
      <AgentActivityTimeline
        activities={done}
        generationStatus="completed"
        completedAt="2026-08-24T00:00:03.800Z"
      />
    );
    act(() => vi.advanceTimersByTime(800));
    const toggle = screen.getByRole('button', { name: /Hoàn tất 3 bước/ });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('Xem hoạt động')).not.toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('Ẩn hoạt động')).not.toBeNull();
  });

  it('keeps failed partial history expanded when a live turn fails', () => {
    const { rerender } = render(
      <AgentActivityTimeline activities={activities} generationStatus="generating" />
    );
    rerender(<AgentActivityTimeline activities={activities} generationStatus="failed" />);
    const toggle = screen.getByRole('button', { name: /Không thể hoàn tất/ });
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('Hiểu yêu cầu')).not.toBeNull();
  });

  it('renders step processing time badge for completed activities', () => {
    const timedActivities: ChatActivity[] = [
      {
        code: 'understanding_request',
        status: 'completed',
        outcome: 'success',
        startedAt: '2026-08-24T00:00:00.000Z',
        completedAt: '2026-08-24T00:00:02.000Z',
      },
      {
        code: 'searching_relevant_information',
        status: 'completed',
        outcome: 'success',
        startedAt: '2026-08-24T00:00:02.000Z',
        completedAt: '2026-08-24T00:00:03.200Z',
      },
    ];

    render(
      <AgentActivityTimeline activities={timedActivities} generationStatus="generating" />
    );

    expect(screen.getByText('2s')).not.toBeNull();
    expect(screen.getByText('1,2s')).not.toBeNull();
  });
});
