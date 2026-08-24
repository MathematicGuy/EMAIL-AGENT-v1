import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ExecutionTraceDrawer } from './ExecutionTraceDrawer';

describe('ExecutionTraceDrawer', () => {
  it('renders reasoning and filenames only', () => {
    const onClose = vi.fn();
    render(<ExecutionTraceDrawer onClose={onClose} trace={{
      provider: 'mimo', model: 'mimo-v2.5-pro', mode: 'reasoning',
      reasoning: 'Compare the supplied values.', reasoningTruncated: false,
      retrievedFilenames: ['brief.pdf', 'notes.docx'],
    }} />);

    expect(screen.getByText('Compare the supplied values.')).toBeTruthy();
    expect(screen.getByText('brief.pdf')).toBeTruthy();
    expect(screen.getByText('notes.docx')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Đóng chi tiết xử lý' }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
