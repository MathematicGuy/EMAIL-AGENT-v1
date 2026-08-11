import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { AgentResult } from '../types';
import { ResultPanel } from './ResultPanel';

afterEach(cleanup);

const metrics = {
  duration_ms: 12,
  worker_version: 'test',
  started_at: '2026-07-27T00:00:00Z',
  completed_at: '2026-07-27T00:00:01Z',
};

describe('ResultPanel', () => {
  it('renders nested invoice records and evidence without requiring raw JSON', () => {
    const result: AgentResult = {
      job_id: 'invoice_job',
      status: 'SUCCEEDED',
      output: {
        records: [
          {
            invoice_number: 'HD-2026-0847',
            grand_total: 182355000,
            line_items: [
              { description: 'Dịch vụ tư vấn AI', quantity: 1, amount_after_tax: 93500000 },
            ],
          },
        ],
        missing_required_fields: [],
        conflicts: [],
      },
      evidence_refs: [
        {
          evidence_id: 'ev_1',
          source_ref_id: 'invoice',
          source_id: 'invoice',
          source_version: '1',
          checksum: 'sha256:test',
          locator: { page: 1 },
          accessed_at: '2026-07-27T00:00:00Z',
          excerpt: 'Tổng tiền thanh toán: 182.355.000',
        },
      ],
      validation_issues: [],
      metrics,
    };

    render(<ResultPanel result={result} />);

    expect(screen.getByText('HD-2026-0847')).toBeTruthy();
    expect(screen.getByText('182.355.000')).toBeTruthy();
    expect(screen.getByText('Dịch vụ tư vấn AI')).toBeTruthy();
    expect(screen.getAllByText(/Tổng tiền thanh toán/).length).toBeGreaterThan(0);
  });

  it('renders five conflict groups as sourced differences without choosing a winner', () => {
    const result: AgentResult = {
      job_id: 'synthesis_job',
      status: 'SUCCEEDED',
      output: {
        summary: 'Hai báo cáo khác phạm vi và quy tắc ghi nhận.',
        conflict_groups: Array.from({ length: 5 }, (_, index) => ({
          metric: `metric_${index + 1}`,
          values: [
            { source: 'business_report', value: 10 + index },
            { source: 'finance_report', value: 9 + index },
          ],
        })),
      },
      evidence_refs: [],
      validation_issues: [
        { code: 'SOURCE_SCOPE_DIFFERENCE', severity: 'INFO', message: 'Khác phạm vi.' },
      ],
      metrics,
    };

    render(<ResultPanel result={result} />);

    expect(screen.getByText('Conflict groups · 5')).toBeTruthy();
    expect(screen.getAllByText(/không tự chọn số đúng/i)).toHaveLength(5);
    expect(screen.getByText('Validation issues · 1')).toBeTruthy();
  });
});
