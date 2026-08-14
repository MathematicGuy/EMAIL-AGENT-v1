import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useStreamingChat, validateAttachmentFile } from './useStreamingChat';

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

function sse(events: unknown[]): Response {
  return new Response(
    events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
  );
}

describe('useStreamingChat Project chat client', () => {
  it('attaches completed RAG evidence to the streamed assistant message', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        return Promise.resolve(json({ session_id: 'session-1', project_id: 'project-1' }, 201));
      }
      if (url.endsWith('/sessions/session-1/messages') && init?.method === 'POST') {
        return Promise.resolve(sse([
          { event_type: 'delta', text: 'Grounded answer' },
          {
            event_type: 'completed',
            retrieval_status: 'success',
            rag_evidence: [{
              source: 'company_knowledge', retrieval_status: 'success', chunk_id: 'chunk-1',
              document_id: 'document-1', document_title: 'Residence guide', section: 'Article 27',
              source_url: null, relevance_score: 0.842, rerank_score: null,
              preview: 'A relevant preview.', content: 'The complete retrieved chunk.',
            }],
          },
        ]));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    await act(async () => result.current.sendMessage('Where can I register?'));

    expect(result.current.messages.at(-1)).toMatchObject({
      role: 'assistant',
      retrievalStatus: 'success',
      ragEvidence: [{ chunkId: 'chunk-1', relevanceScore: 0.842, content: 'The complete retrieved chunk.' }],
    });
  });

  it('creates a Project-bound Cowork session and renders a validated citation', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ project_id: 'project-1' });
        return Promise.resolve(json({ session_id: 'session-1', project_id: 'project-1' }, 201));
      }
      if (url.endsWith('/sessions/session-1/messages') && init?.method === 'POST') {
        return Promise.resolve(sse([
          { event_type: 'delta', text: 'Grounded answer' },
          {
            event_type: 'memory_citation',
            source_id: 'citation-1',
            citation_scope: 'project_document',
            project_id: 'project-1',
            document_id: 'document-1',
            document_title: 'Policy.pdf',
            page_start: 2,
            page_end: 3,
          },
          { event_type: 'completed' },
        ]));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    await act(async () => {
      await result.current.sendMessage('What does the policy say?');
    });

    expect(result.current.messages.at(-1)).toMatchObject({
      role: 'assistant',
      content: 'Grounded answer',
      citations: [{ documentTitle: 'Policy.pdf', pageStart: 2, pageEnd: 3 }],
    });
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/v1/assistant'))).toBe(false);
  });

  it('loads only sessions and history from the active Project', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=project-2')) {
        return Promise.resolve(json({ sessions: [{ session_id: 'session-2', project_id: 'project-2' }] }));
      }
      if (url.endsWith('/sessions/session-2/messages')) {
        return Promise.resolve(json({ turns: [{
          turn_id: 'turn-1',
          user_message: 'Question',
          assistant_message: 'Answer',
          created_at: '2026-08-12T00:00:00Z',
          citation_coordinates: [],
          retrieval_status: 'no_results',
          rag_evidence: [],
        }] }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-2'));
    await waitFor(() => expect(result.current.recentChats).toHaveLength(1));
    await act(async () => result.current.loadExistingChat('session-2'));
    expect(result.current.messages.map((item) => item.content)).toEqual(['Question', 'Answer']);
    expect(result.current.messages.at(-1)).toMatchObject({
      retrievalStatus: 'no_results',
      ragEvidence: [],
    });
  });

  it('uploads composer files persistently to the active Project', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/projects/project-1/documents') && init?.method === 'POST') {
        return Promise.resolve(json({
          document_id: 'document-1',
          status: 'received',
          upload_url: 'https://storage.example/upload',
        }, 202));
      }
      if (url === 'https://storage.example/upload' && init?.method === 'PUT') {
        return Promise.resolve(new Response(null, { status: 200 }));
      }
      if (url.endsWith('/documents/document-1/complete') && init?.method === 'POST') {
        return Promise.resolve(json({ document_id: 'document-1', status: 'received' }, 202));
      }
      if (url.endsWith('/documents/document-1')) {
        return Promise.resolve(json({
          document_id: 'document-1',
          filename: 'policy.pdf',
          media_type: 'application/pdf',
          byte_size: 9,
          status: 'ready',
          error_code: null,
          page_count: 1,
          chunk_count: 1,
          ocr_page_count: 0,
          expires_at: '2026-09-12T00:00:00Z',
        }));
      }
      if (url.endsWith('/projects/project-1/documents')) {
        return Promise.resolve(json({ documents: [] }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));
    act(() => result.current.selectAttachments([
      new File(['%PDF-test'], 'policy.pdf', { type: 'application/pdf' }),
    ]));
    await waitFor(() => expect(result.current.selectedAttachments[0]?.status).toBe('ready'));
    expect(result.current.selectedAttachments[0]?.documentId).toBe('document-1');
  });
});

describe('Project document upload policy', () => {
  it.each([
    ['contract.pdf', 'application/pdf'],
    ['brief.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  ])('accepts %s', (name, type) => {
    expect(validateAttachmentFile(new File(['content'], name, { type }))).toBeNull();
  });

  it.each(['notes.md', 'sales.csv', 'page.html'])('rejects %s', (name) => {
    expect(validateAttachmentFile(new File(['content'], name))).toContain('PDF or DOCX');
  });
});
