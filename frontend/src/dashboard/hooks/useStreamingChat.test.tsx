import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mailCommandProviders, useStreamingChat, validateAttachmentFile } from './useStreamingChat';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
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
  it('parses mail commands case-insensitively with provider union semantics', () => {
    expect(mailCommandProviders('please @EMAIL then @Outlook')).toEqual(['gmail', 'outlook']);
    expect(mailCommandProviders('@MAIL')).toEqual(['gmail', 'outlook']);
    expect(mailCommandProviders('@outlook only')).toEqual(['outlook']);
    expect(mailCommandProviders('ordinary chat')).toEqual([]);
  });

  it('keeps chat A streaming when New is opened and chat B is submitted', async () => {
    const streams = new Map<string, ReadableStreamDefaultController<Uint8Array>>();
    let created = 0;
    const encoder = new TextEncoder();
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        created += 1;
        return Promise.resolve(json({ session_id: `session-${created}`, project_id: 'project-1' }, 201));
      }
      const match = url.match(/\/sessions\/(session-\d+)\/messages$/);
      if (match && init?.method === 'POST') {
        const sessionId = match[1];
        return Promise.resolve(new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            streams.set(sessionId, controller);
            init.signal?.addEventListener('abort', () => controller.error(
              new DOMException('The operation was aborted.', 'AbortError')
            ), { once: true });
          },
        }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    act(() => { void result.current.sendMessage('First prompt'); });
    expect(result.current.messages.map((message) => message.content)).toEqual(['First prompt', '']);
    expect(result.current.isGenerating).toBe(true);
    await waitFor(() => expect(streams.has('session-1')).toBe(true));
    expect(result.current.recentChats[0]).toMatchObject({
      id: 'session-1', title: 'First prompt', generationStatus: 'generating',
    });

    act(() => result.current.resetChat());
    expect(result.current.activeConversationId).toBeNull();
    expect(result.current.messages).toEqual([]);

    act(() => { void result.current.sendMessage('Second prompt'); });
    await waitFor(() => expect(streams.has('session-2')).toBe(true));
    expect(result.current.recentChats.map((chat) => chat.id)).toEqual(['session-2', 'session-1']);
    expect(result.current.recentChats.every((chat) => chat.generationStatus === 'generating')).toBe(true);

    act(() => {
      streams.get('session-1')?.enqueue(encoder.encode('data: {"event_type":"delta","text":"First answer"}\n\n'));
    });
    await act(async () => result.current.loadExistingChat('session-1'));
    expect(result.current.messages.map((message) => message.content)).toEqual([
      'First prompt', 'First answer',
    ]);
    expect(result.current.isGenerating).toBe(true);

    act(() => {
      streams.get('session-1')?.enqueue(encoder.encode('data: {"event_type":"completed"}\n\n'));
      streams.get('session-1')?.close();
    });
    await waitFor(() => expect(result.current.recentChats.find(
      (chat) => chat.id === 'session-1'
    )?.generationStatus).toBe('completed'));
    expect(result.current.recentChats.find((chat) => chat.id === 'session-2')?.generationStatus)
      .toBe('generating');

    act(() => {
      streams.get('session-2')?.enqueue(encoder.encode('data: {"event_type":"completed"}\n\n'));
      streams.get('session-2')?.close();
    });
  });

  it('shows a local generating row immediately and remaps it after slow session creation', async () => {
    let releaseSession: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        return new Promise<Response>((resolve) => { releaseSession = resolve; });
      }
      if (url.endsWith('/sessions/session-slow/messages') && init?.method === 'POST') {
        return Promise.resolve(sse([{ event_type: 'completed' }]));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    let sending: Promise<void> = Promise.resolve();
    act(() => { sending = result.current.sendMessage('Visible before the network'); });
    expect(result.current.recentChats[0]).toMatchObject({
      title: 'Visible before the network', generationStatus: 'generating',
    });
    expect(result.current.recentChats[0]?.id).toMatch(/^__new_chat__/);

    await act(async () => {
      releaseSession?.(json({ session_id: 'session-slow', project_id: 'project-1' }, 201));
      await sending;
    });
    expect(result.current.recentChats.filter((chat) => chat.title === 'Visible before the network'))
      .toHaveLength(1);
    expect(result.current.recentChats[0]?.id).toBe('session-slow');
  });

  it('keeps runtime-backed chats when a sidebar refresh fails', async () => {
    let listCalls = 0;
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) {
        listCalls += 1;
        return listCalls === 1
          ? Promise.resolve(json({ sessions: [] }))
          : Promise.reject(new Error('temporary list failure'));
      }
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        return Promise.resolve(json({ session_id: 'session-kept', project_id: 'project-1' }, 201));
      }
      if (url.endsWith('/sessions/session-kept/messages') && init?.method === 'POST') {
        return Promise.resolve(sse([{ event_type: 'completed' }]));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    await act(async () => result.current.sendMessage('Keep me visible'));
    await waitFor(() => expect(listCalls).toBe(2));

    expect(result.current.recentChats).toEqual([expect.objectContaining({
      id: 'session-kept', title: 'Keep me visible', generationStatus: 'completed',
    })]);
  });

  it('keeps drafts scoped to each chat for the current page session', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [{
        session_id: 'session-a', project_id: 'project-1', title: 'Chat A',
      }] }));
      if (url.endsWith('/sessions/session-a/messages')) return Promise.resolve(json({ turns: [] }));
      if (url.endsWith('/projects/project-1/documents') && init?.method === 'POST') {
        return new Promise<Response>(() => undefined);
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.recentChats).toHaveLength(1));

    act(() => result.current.setInputText('new-chat draft'));
    act(() => result.current.selectAttachments([
      new File(['bad'], 'notes.md'),
      new File(['%PDF'], 'draft.pdf', { type: 'application/pdf' }),
    ]));
    expect(result.current.selectedAttachments.map((item) => item.name)).toEqual(['draft.pdf']);
    expect(result.current.attachmentError).toContain('PDF or DOCX');
    await act(async () => result.current.loadExistingChat('session-a'));
    expect(result.current.selectedAttachments).toEqual([]);
    expect(result.current.attachmentError).toBeNull();
    act(() => result.current.setInputText('chat-a draft'));
    act(() => result.current.resetChat());
    expect(result.current.inputText).toBe('new-chat draft');
    expect(result.current.selectedAttachments.map((item) => item.name)).toEqual(['draft.pdf']);
    expect(result.current.attachmentError).toContain('PDF or DOCX');

    await act(async () => result.current.loadExistingChat('session-a'));
    expect(result.current.inputText).toBe('chat-a draft');
  });

  it('retries the same logical turn without duplicating its user prompt', async () => {
    const keys: string[] = [];
    let attempts = 0;
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        return Promise.resolve(json({ session_id: 'session-1', project_id: 'project-1' }, 201));
      }
      if (url.endsWith('/sessions/session-1/messages') && init?.method === 'POST') {
        keys.push((JSON.parse(String(init.body)) as { idempotency_key: string }).idempotency_key);
        attempts += 1;
        return Promise.resolve(attempts === 1
          ? sse([{ event_type: 'started', turn_id: 'turn-1' }, {
              event_type: 'error', status: 'failed', safe_message: 'Try again.',
            }])
          : sse([{ event_type: 'started', turn_id: 'turn-1' }, {
              event_type: 'delta', text: 'Recovered answer',
            }, { event_type: 'completed' }]));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));
    await act(async () => result.current.sendMessage('Keep this prompt once'));
    const failedId = result.current.messages.at(-1)?.id;
    expect(result.current.messages.at(-1)?.generationStatus).toBe('failed');

    await act(async () => result.current.retryTurn(failedId as string));

    expect(keys[1]).toBe(keys[0]);
    expect(result.current.messages.filter((message) => message.role === 'user')).toHaveLength(1);
    expect(result.current.messages.at(-1)).toMatchObject({
      content: 'Recovered answer', generationStatus: 'completed', isStreaming: false,
    });
  });

  it('runs @email against the selected Gmail inbox and reports its result', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        return Promise.resolve(json({ session_id: 'session-1', project_id: 'project-1' }, 201));
      }
      if (url.endsWith('/v1/mail-todo/connections')) {
        return Promise.resolve(json({ connections: [{ id: 'mailbox-1', provider: 'gmail', status: 'active' }] }));
      }
      if (url.endsWith('/v1/mail-todo/runs') && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({
          mailboxConnectionId: 'mailbox-1',
          maxEmails: 10,
          query: 'is:unread in:inbox category:primary',
        });
        return Promise.resolve(json({ id: 'run-1', status: 'queued', statusUrl: '/v1/mail-todo/runs/run-1' }, 202));
      }
      if (url.endsWith('/v1/mail-todo/runs/run-1')) {
        return Promise.resolve(json({
          id: 'run-1', status: 'succeeded',
          progress: {
            emailsMatched: 4, emailsProcessed: 4, emailsToProcess: 4, maxEmails: 10,
            filteredSummary: 'Lưu ý: LLM xác định email còn lại là bản tin cập nhật.',
          },
          error: null,
        }));
      }
      if (url.endsWith('/v1/mail-todo/runs/run-1/tasks')) {
        return Promise.resolve(json({ tasks: [{ task_id: 'task-1' }, { task_id: 'task-2' }] }));
      }
      if (url.endsWith('/sessions/session-1/mail-scans') && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toMatchObject({
          turn_id: expect.stringMatching(/^assistant-/),
          user_message: '@email quét giúp tôi',
          assistant_message: 'Gmail: Đã quét xong: đã quét 4 email và tạo 2 action item. Lưu ý: LLM xác định email còn lại là bản tin cập nhật.',
          mail_scan: {
            status: 'succeeded', emails_matched: 4, emails_processed: 4,
            emails_to_process: 4, action_items_count: 2,
          },
        });
        return Promise.resolve(json({ turn_id: 'mail-turn-1' }, 201));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    await act(async () => result.current.sendMessage('@email quét giúp tôi'));

    expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url).endsWith('/sessions/session-1/mail-scans') && (init as RequestInit).method === 'POST'
    )).toBe(true);
    expect(result.current.messages.at(-1)).toMatchObject({
      role: 'assistant',
      content: 'Gmail: Đã quét xong: đã quét 4 email và tạo 2 action item. Lưu ý: LLM xác định email còn lại là bản tin cập nhật.',
      isStreaming: false,
      mailScan: { status: 'succeeded', emailsProcessed: 4, actionItemsCount: 2 },
    });
  });

  it('records a failed mail scan when no Gmail account is connected', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        return Promise.resolve(json({ session_id: 'session-1', project_id: 'project-1' }, 201));
      }
      if (url.endsWith('/v1/mail-todo/connections')) return Promise.resolve(json({ connections: [] }));
      if (url.endsWith('/sessions/session-1/mail-scans') && init?.method === 'POST') {
        return Promise.resolve(json({ turn_id: 'mail-turn-1' }, 201));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    await act(async () => result.current.sendMessage('@email'));

    expect(result.current.messages.at(-1)).toMatchObject({
      role: 'assistant',
      isStreaming: false,
      mailScan: { status: 'failed' },
    });
    expect(result.current.messages.at(-1)?.content).toContain('Chưa có tài khoản Gmail');
  });

  it('marks @mail partial when Gmail succeeds and Outlook is missing', async () => {
    let persisted = 0;
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        return Promise.resolve(json({ session_id: 'session-1', project_id: 'project-1' }, 201));
      }
      if (url.endsWith('/v1/mail-todo/connections')) return Promise.resolve(json({
        connections: [{ id: 'gmail-1', provider: 'gmail', status: 'active' }],
      }));
      if (url.endsWith('/v1/mail-todo/runs') && init?.method === 'POST') {
        return Promise.resolve(json({ id: 'gmail-run', status: 'queued', statusUrl: '/runs/gmail-run' }, 202));
      }
      if (url.endsWith('/v1/mail-todo/runs/gmail-run')) return Promise.resolve(json({
        id: 'gmail-run', status: 'succeeded',
        progress: { emailsMatched: 2, emailsProcessed: 2, emailsToProcess: 2, maxEmails: 10 },
        error: null,
      }));
      if (url.endsWith('/v1/mail-todo/runs/gmail-run/tasks')) {
        return Promise.resolve(json({ tasks: [{ task_id: 'task-1' }] }));
      }
      if (url.endsWith('/sessions/session-1/mail-scans') && init?.method === 'POST') {
        persisted += 1;
        return Promise.resolve(json({ turn_id: 'mail-turn-1' }, 201));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    await act(async () => result.current.sendMessage('@MAIL'));

    expect(result.current.messages.at(-1)).toMatchObject({
      isStreaming: false,
      mailScan: { status: 'partial', emailsProcessed: 2, actionItemsCount: 1 },
    });
    expect(result.current.messages.at(-1)?.content).toContain('Outlook: Chưa có tài khoản Outlook');
    expect(persisted).toBe(1);
  });

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

  it('finalizes an in-flight assistant turn when generation is stopped', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        return Promise.resolve(json({ session_id: 'session-1', project_id: 'project-1' }, 201));
      }
      if (url.endsWith('/sessions/session-1/turns/cancel') && init?.method === 'POST') {
        return Promise.resolve(json({ status: 'cancelled' }, 202));
      }
      if (url.endsWith('/sessions/session-1/messages') && init?.method === 'POST') {
        return new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => {
            reject(new DOMException('The operation was aborted.', 'AbortError'));
          }, { once: true });
        });
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    act(() => { void result.current.sendMessage('Please draft a response.'); });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) =>
      String(url).endsWith('/sessions/session-1/messages')
    )).toBe(true));

    act(() => result.current.stopGeneration());

    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url).endsWith('/sessions/session-1/turns/cancel') &&
      JSON.parse(String((init as RequestInit).body)).idempotency_key
    )).toBe(true));
    await waitFor(() => expect(result.current.messages.at(-1)).toMatchObject({
      role: 'assistant',
      content: 'Chat cancelled.',
      isStreaming: false,
      generationStatus: 'cancelled',
    }));
  });

  it('stops only the active chat while another chat continues generating', async () => {
    const streams = new Map<string, ReadableStreamDefaultController<Uint8Array>>();
    let created = 0;
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        created += 1;
        return Promise.resolve(json({ session_id: `session-${created}`, project_id: 'project-1' }, 201));
      }
      if (/\/sessions\/session-\d+\/turns\/cancel$/.test(url) && init?.method === 'POST') {
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      const match = url.match(/\/sessions\/(session-\d+)\/messages$/);
      if (match && init?.method === 'POST') {
        return Promise.resolve(new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            streams.set(match[1], controller);
            init.signal?.addEventListener('abort', () => controller.error(
              new DOMException('The operation was aborted.', 'AbortError')
            ), { once: true });
          },
        }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    act(() => { void result.current.sendMessage('First prompt'); });
    await waitFor(() => expect(streams.has('session-1')).toBe(true));
    act(() => result.current.resetChat());
    act(() => { void result.current.sendMessage('Second prompt'); });
    await waitFor(() => expect(streams.has('session-2')).toBe(true));
    await act(async () => result.current.loadExistingChat('session-1'));

    act(() => result.current.stopGeneration());

    await waitFor(() => expect(result.current.recentChats.find(
      (chat) => chat.id === 'session-1'
    )?.generationStatus).toBe('cancelled'));
    expect(result.current.recentChats.find((chat) => chat.id === 'session-2')?.generationStatus)
      .toBe('generating');
    const cancelUrls = fetchMock.mock.calls
      .filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
      .map(([url]) => String(url))
      .filter((url) => url.endsWith('/turns/cancel'));
    expect(cancelUrls).toEqual([
      expect.stringContaining('/sessions/session-1/turns/cancel'),
    ]);

    act(() => {
      streams.get('session-2')?.enqueue(new TextEncoder().encode(
        'data: {"event_type":"completed"}\n\n'
      ));
      streams.get('session-2')?.close();
    });
    await waitFor(() => expect(result.current.recentChats.find(
      (chat) => chat.id === 'session-2'
    )?.generationStatus).toBe('completed'));
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
        return Promise.resolve(json({ sessions: [{
          session_id: 'session-2', project_id: 'project-2', title: 'Project rollout plan',
        }] }));
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
          mail_scan: {
            status: 'succeeded', emails_matched: 10, emails_processed: 10,
            emails_to_process: 10, action_items_count: 5,
          },
        }] }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-2'));
    await waitFor(() => expect(result.current.recentChats).toHaveLength(1));
    expect(result.current.recentChats[0]?.title).toBe('Project rollout plan');
    await act(async () => result.current.loadExistingChat('session-2'));
    expect(result.current.messages.map((item) => item.content)).toEqual(['Question', 'Answer']);
    expect(result.current.messages.at(-1)).toMatchObject({
      retrievalStatus: 'no_results',
      ragEvidence: [],
      mailScan: { status: 'succeeded', emailsProcessed: 10, actionItemsCount: 5 },
    });
  });

  it('keeps a selected cross-Project chat active when the Project prop catches up', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=')) return Promise.resolve(json({ sessions: [] }));
      if (url.endsWith('/sessions/session-2/messages')) return Promise.resolve(json({ turns: [{
        turn_id: 'turn-2', user_message: 'Project two prompt', assistant_message: 'Project two answer',
        created_at: '2026-08-17T00:00:00Z', status: 'completed',
      }] }));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result, rerender } = renderHook(
      ({ projectId }) => useStreamingChat('gemini', projectId),
      { initialProps: { projectId: 'project-1' } },
    );
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));

    await act(async () => result.current.loadExistingChat('session-2', 'project-2'));
    rerender({ projectId: 'project-2' });

    await waitFor(() => expect(result.current.activeConversationId).toBe('session-2'));
    expect(result.current.messages.map((message) => message.content)).toEqual([
      'Project two prompt', 'Project two answer',
    ]);
  });

  it('keeps list-history RAG evidence when the payload omits chunk content', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=project-1')) {
        return Promise.resolve(json({ sessions: [{
          session_id: 'session-slim', project_id: 'project-1', title: 'Slim history',
        }] }));
      }
      if (url.includes('/sessions/session-slim/messages') && !url.includes('include_content=true')) {
        return Promise.resolve(json({ turns: [{
          turn_id: 'turn-slim',
          user_message: 'What is the policy?',
          assistant_message: 'See the retrieved policy.',
          created_at: '2026-08-17T00:00:00Z',
          citation_coordinates: [],
          retrieval_status: 'success',
          rag_evidence: [{
            source: 'company_knowledge',
            retrieval_status: 'success',
            chunk_id: 'chunk-slim',
            document_id: 'doc-slim',
            document_title: 'Policy.md',
            section: 'Overview',
            source_url: null,
            relevance_score: 0.81,
            rerank_score: 0.77,
            preview: 'Short preview of the chunk.',
          }],
        }] }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.recentChats).toHaveLength(1));
    await act(async () => result.current.loadExistingChat('session-slim'));
    expect(result.current.messages.at(-1)).toMatchObject({
      retrievalStatus: 'success',
      ragEvidence: [{
        chunkId: 'chunk-slim',
        preview: 'Short preview of the chunk.',
        content: '',
      }],
    });
  });

  it('deletes a saved chat and clears it from the active view', async () => {
    let sessions = [{
      session_id: 'session-1', project_id: 'project-1', title: 'Delete me',
    }];
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=project-1')) {
        return Promise.resolve(json({ sessions }));
      }
      if (url.endsWith('/sessions/session-1/messages')) {
        return Promise.resolve(json({ turns: [{
          turn_id: 'turn-1', user_message: 'Question', assistant_message: 'Answer',
          created_at: '2026-08-12T00:00:00Z', citation_coordinates: [], rag_evidence: [],
          retrieval_status: null,
        }] }));
      }
      if (url.endsWith('/sessions/session-1') && init?.method === 'DELETE') {
        sessions = [];
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.recentChats).toHaveLength(1));
    await act(async () => result.current.loadExistingChat('session-1'));

    await act(async () => {
      await result.current.deleteChat('session-1');
    });

    expect(fetchMock.mock.calls.some(([url, init]) =>
      String(url).endsWith('/sessions/session-1') && (init as RequestInit | undefined)?.method === 'DELETE'
    )).toBe(true);
    expect(result.current.messages).toEqual([]);
    expect(result.current.activeConversationId).toBeNull();
    expect(result.current.recentChats).toEqual([]);
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

  it('highlights the clicked chat and clears stale messages before history returns', async () => {
    let releaseMessages: ((value: Response) => void) | undefined;
    const blocked = new Promise<Response>((resolve) => {
      releaseMessages = resolve;
    });
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=project-1')) {
        return Promise.resolve(json({ sessions: [
          { session_id: 'session-a', project_id: 'project-1', title: 'Chat A' },
          { session_id: 'session-b', project_id: 'project-1', title: 'Chat B' },
        ] }));
      }
      if (url.endsWith('/sessions/session-a/messages')) {
        return Promise.resolve(json({ turns: [{
          turn_id: 'turn-a', user_message: 'Question A', assistant_message: 'Answer A',
          created_at: '2026-08-12T00:00:00Z', citation_coordinates: [], rag_evidence: [],
          retrieval_status: null,
        }] }));
      }
      if (url.endsWith('/sessions/session-b/messages')) {
        return blocked;
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.recentChats).toHaveLength(2));
    await act(async () => result.current.loadExistingChat('session-a'));
    expect(result.current.messages.map((item) => item.content)).toEqual(['Question A', 'Answer A']);

    let switchPromise: Promise<void> = Promise.resolve();
    act(() => {
      switchPromise = result.current.loadExistingChat('session-b');
    });

    expect(result.current.activeConversationId).toBe('session-b');
    expect(result.current.messages).toEqual([]);
    expect(result.current.isTranscriptLoading).toBe(true);
    expect(result.current.isSessionListLoading).toBe(false);
    expect(result.current.isHistoryLoading).toBe(false);

    await act(async () => {
      releaseMessages?.(json({ turns: [{
        turn_id: 'turn-b', user_message: 'Question B', assistant_message: 'Answer B',
        created_at: '2026-08-12T00:01:00Z', citation_coordinates: [], rag_evidence: [],
        retrieval_status: null,
      }] }));
      await switchPromise;
    });

    expect(result.current.isTranscriptLoading).toBe(false);
    expect(result.current.messages.map((item) => item.content)).toEqual(['Question B', 'Answer B']);
  });

  it('paints a previously loaded chat from memory before the refetch returns', async () => {
    let releaseA: ((value: Response) => void) | undefined;
    let aLoads = 0;
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=project-1')) {
        return Promise.resolve(json({ sessions: [
          { session_id: 'session-a', project_id: 'project-1', title: 'Chat A' },
          { session_id: 'session-b', project_id: 'project-1', title: 'Chat B' },
        ] }));
      }
      if (url.endsWith('/sessions/session-a/messages')) {
        aLoads += 1;
        if (aLoads === 1) {
          return Promise.resolve(json({ turns: [{
            turn_id: 'turn-a', user_message: 'Question A', assistant_message: 'Answer A',
            created_at: '2026-08-12T00:00:00Z', citation_coordinates: [], rag_evidence: [],
            retrieval_status: null,
          }] }));
        }
        return new Promise<Response>((resolve) => {
          releaseA = resolve;
        });
      }
      if (url.endsWith('/sessions/session-b/messages')) {
        return Promise.resolve(json({ turns: [{
          turn_id: 'turn-b', user_message: 'Question B', assistant_message: 'Answer B',
          created_at: '2026-08-12T00:01:00Z', citation_coordinates: [], rag_evidence: [],
          retrieval_status: null,
        }] }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.recentChats).toHaveLength(2));
    await act(async () => result.current.loadExistingChat('session-a'));
    await act(async () => result.current.loadExistingChat('session-b'));

    act(() => {
      void result.current.loadExistingChat('session-a');
    });

    expect(result.current.activeConversationId).toBe('session-a');
    expect(result.current.messages.map((item) => item.content)).toEqual(['Question A', 'Answer A']);
    expect(result.current.isTranscriptLoading).toBe(false);

    await act(async () => {
      releaseA?.(json({ turns: [{
        turn_id: 'turn-a2', user_message: 'Question A2', assistant_message: 'Answer A2',
        created_at: '2026-08-12T00:02:00Z', citation_coordinates: [], rag_evidence: [],
        retrieval_status: null,
      }] }));
    });
    await waitFor(() => {
      expect(result.current.messages.map((item) => item.content)).toEqual(['Question A2', 'Answer A2']);
    });
  });

  it('keeps the just-sent turn when the current Recents item is re-clicked', async () => {
    let releaseReload!: () => void;
    const reloadTurn = new Promise<void>((resolve) => {
      releaseReload = resolve;
    });
    let session1Gets = 0;
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=project-1')) {
        return Promise.resolve(json({
          sessions: [
            { session_id: 'session-1', project_id: 'project-1', title: 'Current' },
          ],
        }));
      }
      if (url.endsWith('/sessions/session-1/messages') && init?.method === 'POST') {
        return Promise.resolve(sse([
          { event_type: 'delta', text: 'Just sent answer' },
          { event_type: 'completed' },
        ]));
      }
      if (url.endsWith('/sessions/session-1/messages')) {
        session1Gets += 1;
        if (session1Gets === 1) {
          return Promise.resolve(json({ turns: [{
            turn_id: 'turn-1', user_message: 'Earlier question', assistant_message: 'Earlier answer',
            created_at: '2026-08-12T00:00:00Z', citation_coordinates: [], rag_evidence: [],
          }] }));
        }
        return reloadTurn.then(() => json({ turns: [
          {
            turn_id: 'turn-1', user_message: 'Earlier question', assistant_message: 'Earlier answer',
            created_at: '2026-08-12T00:00:00Z', citation_coordinates: [], rag_evidence: [],
          },
          {
            turn_id: 'turn-2', user_message: 'Just sent question', assistant_message: 'Server answer',
            created_at: '2026-08-12T00:01:00Z', citation_coordinates: [], rag_evidence: [],
          },
        ] }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.isHistoryLoading).toBe(false));
    await act(async () => result.current.loadExistingChat('session-1'));

    await act(async () => result.current.sendMessage('Just sent question'));
    expect(result.current.messages.map((item) => item.content)).toEqual([
      'Earlier question',
      'Earlier answer',
      'Just sent question',
      'Just sent answer',
    ]);

    let pending: Promise<void> | undefined;
    act(() => {
      pending = result.current.loadExistingChat('session-1');
    });
    expect(result.current.activeConversationId).toBe('session-1');
    expect(result.current.messages.map((item) => item.content)).toEqual([
      'Earlier question',
      'Earlier answer',
      'Just sent question',
      'Just sent answer',
    ]);
    expect(result.current.isTranscriptLoading).toBe(false);

    await act(async () => {
      releaseReload();
      await pending;
    });
    expect(result.current.messages.some((item) => item.content === 'Just sent question')).toBe(true);
    expect(result.current.messages.some((item) =>
      item.role === 'assistant' && (item.content === 'Just sent answer' || item.content === 'Server answer')
    )).toBe(true);
  });

  it('prefetches another chat into memory without changing the visible transcript', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/sessions?project_id=project-1')) {
        return Promise.resolve(json({ sessions: [
          { session_id: 'session-a', project_id: 'project-1', title: 'Chat A' },
          { session_id: 'session-b', project_id: 'project-1', title: 'Chat B' },
        ] }));
      }
      if (url.endsWith('/sessions/session-a/messages')) {
        return Promise.resolve(json({ turns: [{
          turn_id: 'turn-a', user_message: 'Question A', assistant_message: 'Answer A',
          created_at: '2026-08-12T00:00:00Z', citation_coordinates: [], rag_evidence: [],
          retrieval_status: null,
        }] }));
      }
      if (url.endsWith('/sessions/session-b/messages')) {
        return Promise.resolve(json({ turns: [{
          turn_id: 'turn-b', user_message: 'Question B', assistant_message: 'Answer B',
          created_at: '2026-08-12T00:01:00Z', citation_coordinates: [], rag_evidence: [],
          retrieval_status: null,
        }] }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { result } = renderHook(() => useStreamingChat('gemini', 'project-1'));
    await waitFor(() => expect(result.current.recentChats).toHaveLength(2));
    await act(async () => result.current.loadExistingChat('session-a'));
    expect(result.current.messages.map((item) => item.content)).toEqual(['Question A', 'Answer A']);

    await act(async () => result.current.prefetchChat('session-b'));
    expect(result.current.activeConversationId).toBe('session-a');
    expect(result.current.messages.map((item) => item.content)).toEqual(['Question A', 'Answer A']);

    act(() => {
      void result.current.loadExistingChat('session-b');
    });
    expect(result.current.messages.map((item) => item.content)).toEqual(['Question B', 'Answer B']);
    expect(result.current.isTranscriptLoading).toBe(false);
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
