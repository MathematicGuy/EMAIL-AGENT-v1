import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { InlineReasoningCard } from './InlineReasoningCard';
import type { ChatExecutionTrace } from '../types';

const reasoningTrace: ChatExecutionTrace = {
  provider: 'mimo',
  model: 'mimo-v2.5-pro',
  mode: 'reasoning',
  reasoning: 'Bước 1: Đọc yêu cầu.\nBước 2: Trả lời.',
  reasoningTruncated: false,
  retrievedFilenames: [],
};

const writeText = vi.fn();

beforeEach(() => {
  vi.useFakeTimers();
  writeText.mockReset();
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('InlineReasoningCard', () => {
  it('renders nothing for a turn without reasoning or fast mode', () => {
    const { container } = render(<InlineReasoningCard generationStatus="completed" />);
    expect(container.firstChild).toBeNull();
  });

  it('shows a live stopwatch expanded while generating', () => {
    render(<InlineReasoningCard generationStatus="generating" />);

    expect(screen.getByRole('button', { expanded: true })).toBeTruthy();
    expect(screen.getByText(/Đang suy luận\.\.\. \(0,0s\)/)).toBeTruthy();
    expect(screen.getByText('Đang chờ mô hình trả chuỗi suy luận...')).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(1500);
    });

    expect(screen.getByText(/Đang suy luận\.\.\. \(1,5s\)/)).toBeTruthy();
  });

  it('auto-collapses into a duration pill when generation completes', () => {
    const { rerender } = render(<InlineReasoningCard generationStatus="generating" />);

    act(() => {
      vi.advanceTimersByTime(3800);
    });
    rerender(<InlineReasoningCard generationStatus="completed" executionTrace={reasoningTrace} />);

    const toggle = screen.getByRole('button', { expanded: false });
    expect(toggle.textContent).toContain('Đã suy luận trong 3,8 giây');
    expect(toggle.textContent).toContain('mimo-v2.5-pro');
    expect(screen.queryByText(/Bước 1: Đọc yêu cầu/)).toBeNull();
  });

  it('expands and collapses the chain of thought on click', () => {
    render(<InlineReasoningCard generationStatus="completed" executionTrace={reasoningTrace} />);

    const toggle = screen.getByRole('button', { expanded: false });
    fireEvent.click(toggle);

    expect(screen.getByText(/Bước 1: Đọc yêu cầu/)).toBeTruthy();
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText(/Bước 1: Đọc yêu cầu/)).toBeNull();
  });

  it('copies the reasoning trace and confirms the copy', () => {
    render(<InlineReasoningCard generationStatus="completed" executionTrace={reasoningTrace} />);

    fireEvent.click(screen.getByRole('button', { expanded: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Sao chép chuỗi suy luận' }));

    expect(writeText).toHaveBeenCalledWith(reasoningTrace.reasoning);
    expect(screen.getByText('Đã sao chép!')).toBeTruthy();
  });

  it('explains that thinking is disabled in fast mode', () => {
    render(
      <InlineReasoningCard
        generationStatus="completed"
        executionTrace={{ ...reasoningTrace, mode: 'fast', reasoning: undefined }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { expanded: false }));
    expect(screen.getByText(/Chế độ Nhanh:/)).toBeTruthy();
  });
});
