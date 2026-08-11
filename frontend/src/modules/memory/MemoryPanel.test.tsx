import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MemoryPanel } from './MemoryPanel';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('MemoryPanel', () => {
  it('loads scoped memories and requires a confirmation before delete', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            count: 1,
            items: [
              {
                memory_id: 'mem-a',
                memory_type: 'project',
                content: 'Weekly delivery review',
                scope_type: 'PROJECT',
                scope_id: 'demo-project',
                authority: 'project-owner',
                verification: 'VERIFIED',
                status: 'ACTIVE',
                version: 1,
                source_refs: [{ source_id: 'source-a' }],
                conflict_group_id: null,
                updated_at: '2026-07-24T08:00:00Z'
              }
            ]
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            memory_id: 'mem-a',
            memory_type: 'project',
            content: 'Weekly delivery review',
            scope_type: 'PROJECT',
            scope_id: 'demo-project',
            authority: 'project-owner',
            verification: 'VERIFIED',
            status: 'DELETED',
            version: 2,
            source_refs: [{ source_id: 'source-a' }],
            conflict_group_id: null,
            updated_at: '2026-07-24T08:01:00Z'
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      );
    vi.stubGlobal('fetch', fetchMock);

    render(<MemoryPanel isOpen onClose={() => undefined} />);
    expect(await screen.findByText('Weekly delivery review')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Delete memory mem-a'));
    expect(screen.getByText(/Preview: memory này sẽ chuyển/)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText('Confirm invalidate'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('DELETED')).toBeTruthy();
  });

  it('does not render when closed', () => {
    render(<MemoryPanel isOpen={false} onClose={() => undefined} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows summary, claim groups, sources, and retryable LLM errors', async () => {
    const claim = {
      claim_id: 'claim-a',
      canonical_key: 'delivery.review',
      category: 'preference',
      content: 'Weekly delivery review',
      confidence: 0.9,
      sensitivity: 'INTERNAL',
      status: 'PROPOSED',
      explicit: false,
      version: 1,
      supersedes_claim_id: null,
      conflict_group_id: null,
      evidence_count: 1,
      independent_run_count: 1,
      decision_reason: 'new fact',
      updated_at: '2026-07-24T08:00:00Z'
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/v1/memories?')) {
        return new Response(JSON.stringify({ count: 0, items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      if (url.includes('/v1/memory-summary:refresh')) {
        return new Response(
          JSON.stringify({
            error_code: 'MEMORY_LLM_UNAVAILABLE',
            user_message: 'Memory LLM is unavailable.'
          }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/v1/memory-summary?')) {
        return new Response(
          JSON.stringify({
            summary_id: 'summary-a',
            content: 'Project review memory.',
            sections: [],
            claim_ids: [],
            version: 1,
            freshness: 'STALE',
            generated_at: '2026-07-24T08:00:00Z',
            pending_job_count: 1
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/sources')) {
        return new Response(
          JSON.stringify({
            claim_id: 'claim-a',
            count: 1,
            items: [
              {
                evidence_id: 'evidence-a',
                observation_id: 'observation-a',
                source_kind: 'RUN_CONTEXT',
                source_id: 'event-a',
                source_run_id: 'run-a',
                stance: 'SUPPORTS',
                created_at: '2026-07-24T08:00:00Z'
              }
            ]
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/v1/memory-claims')) {
        return new Response(JSON.stringify({ count: 1, items: [claim] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      throw new Error(`Unexpected request: ${url} ${init?.method ?? 'GET'}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<MemoryPanel isOpen onClose={() => undefined} />);
    fireEvent.click(screen.getByText('Project Memory'));

    expect(await screen.findByText('Project review memory.')).toBeTruthy();
    expect(screen.getByText('STALE')).toBeTruthy();
    expect(screen.getByText('Weekly delivery review')).toBeTruthy();

    fireEvent.click(screen.getByText(/Show sources/));
    expect(await screen.findByText(/SUPPORTS · RUN_CONTEXT · run-a/)).toBeTruthy();

    fireEvent.click(screen.getByText('LLM refresh'));
    expect(
      await screen.findByText(/MEMORY_LLM_UNAVAILABLE/)
    ).toBeTruthy();
  });
});
