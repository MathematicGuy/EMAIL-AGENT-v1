import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import DocumentsDemo from './DocumentsDemo';

afterEach(cleanup);

describe('DocumentsDemo', () => {
  it('presents four production workflow presets without AgentTask JSON', () => {
    render(<DocumentsDemo />);

    expect(screen.getAllByText('Invoice Extract').length).toBeGreaterThan(0);
    expect(screen.getByText('Meeting Extract')).toBeTruthy();
    expect(screen.getByText('Document Query')).toBeTruthy();
    expect(screen.getByText('General Document/Data Analysis')).toBeTruthy();
    expect(screen.queryByText('AgentTask')).toBeNull();
  });

  it('shows an objective for Module 1 instead of raw profile constraints', () => {
    render(<DocumentsDemo />);

    expect(screen.queryByDisplayValue(/invoice_vn/)).toBeNull();
    expect(screen.getByLabelText(/mục tiêu workflow/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /advanced/i })).toBeNull();
  });

  it('enforces the one-file requirement for document query', () => {
    render(<DocumentsDemo />);
    fireEvent.click(screen.getByRole('button', { name: /document query/i }));

    expect(screen.getAllByText(/1 file/i).length).toBeGreaterThan(0);
    expect(
      screen.getByRole('button', { name: /tạo kế hoạch/i }).hasAttribute('disabled')
    ).toBe(true);
  });
});
