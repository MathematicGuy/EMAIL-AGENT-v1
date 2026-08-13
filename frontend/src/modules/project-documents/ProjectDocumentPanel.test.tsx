import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectDocumentPanel } from './ProjectDocumentPanel';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('ProjectDocumentPanel', () => {
  it('allows a processing document to be deleted', async () => {
    const fetchMock = vi.fn().mockImplementation(
      (_input: string | URL | Request, init?: RequestInit) => {
        if (init?.method === 'DELETE') return Promise.resolve(json({ status: 'deleting' }, 202));
        return Promise.resolve(json({ documents: [{
          document_id: 'document-1',
          filename: 'policy.pdf',
          media_type: 'application/pdf',
          byte_size: 10,
          status: 'indexing',
          error_code: null,
          page_count: 0,
          chunk_count: 0,
          ocr_page_count: 0,
          expires_at: '2026-09-12T00:00:00Z',
        }] }));
      }
    );
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('confirm', vi.fn(() => true));
    render(<ProjectDocumentPanel projectId="project-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Project documents' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete policy.pdf' }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) =>
      (init as RequestInit | undefined)?.method === 'DELETE'
    )).toBe(true));
  });

  it('explains a native PDF extraction failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ documents: [{
      document_id: 'document-1',
      filename: 'report.pdf',
      media_type: 'application/pdf',
      byte_size: 10,
      status: 'failed',
      error_code: 'native_extraction_failed',
      page_count: 0,
      chunk_count: 0,
      ocr_page_count: 0,
      expires_at: '2026-09-12T00:00:00Z',
    }] })));
    render(<ProjectDocumentPanel projectId="project-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Project documents' }));

    expect(await screen.findByText(/PDF text could not be extracted/i)).toBeTruthy();
  });
});
