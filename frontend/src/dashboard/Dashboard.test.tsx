import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Dashboard from './Dashboard';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function response(payload: unknown, status = 200): Response {
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

function noContent(): Response {
  return new Response(null, { status: 204 });
}

function projectFetch(extra?: (url: string, init?: RequestInit) => Response | undefined) {
  return vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const custom = extra?.(url, init);
    if (custom) return Promise.resolve(custom);
    if (url.endsWith('/v1/cowork/chat/guest-session') && init?.method === 'POST') {
      return Promise.resolve(noContent());
    }
    if (url.endsWith('/v1/cowork/chat/projects')) {
      return Promise.resolve(response({ projects: [{
        project_id: 'project-default',
        name: 'Default Project',
        is_default: true,
        created_at: '2026-08-12T00:00:00Z',
      }] }));
    }
    if (url.includes('/v1/cowork/chat/sessions?project_id=')) {
      return Promise.resolve(response({ sessions: [] }));
    }
    if (url.endsWith('/v1/cowork/chat/document-health')) {
      return Promise.resolve(response({ status: 'ready', checks: { feature: 'enabled' } }));
    }
    if (url.endsWith('/api/v1/health') || url.endsWith('/health')) {
      return Promise.resolve(response({ status: 'ok' }));
    }
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  });
}

describe('Dashboard Project chat', () => {
  it('renders the main chat and allows typing', async () => {
    vi.stubGlobal('fetch', projectFetch());
    render(<Dashboard />);
    const input = screen.getByPlaceholderText('Tôi có thể giúp gì cho bạn hôm nay?');
    fireEvent.change(input, { target: { value: 'Xin chào AI' } });
    expect((input as HTMLTextAreaElement).value).toBe('Xin chào AI');
    expect((await screen.findAllByText('Default Project')).length).toBeGreaterThan(0);
  });

  it('creates and selects the Default Project when a new chat starts without one', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/v1/cowork/chat/guest-session') && init?.method === 'POST') {
        return Promise.resolve(noContent());
      }
      if (url.endsWith('/v1/cowork/chat/projects')) {
        return Promise.resolve(response({ projects: [{
          project_id: 'project-default',
          name: 'Default Project',
          is_default: true,
          created_at: '2026-08-17T00:00:00Z',
        }] }));
      }
      if (url.endsWith('/v1/cowork/chat/document-health')) {
        return Promise.resolve(response({ status: 'ready', checks: { feature: 'enabled' } }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    window.localStorage.clear();

    render(<Dashboard />);
    // The sidebar renders expanded by default, where the global new-chat control
    // is an untitled button labelled "Tạo cuộc trò chuyện mới" (Taskbar.tsx).
    fireEvent.click(screen.getByRole('button', { name: 'Tạo cuộc trò chuyện mới' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/backend/v1/cowork/chat/guest-session',
        expect.objectContaining({ method: 'POST', credentials: 'include' })
      );
    });
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/backend/v1/cowork/chat/projects',
      expect.objectContaining({ method: 'POST' })
    );
    expect((await screen.findAllByText('Default Project')).length).toBeGreaterThan(0);
  });

  it('creates the Default Project before sending a first chat message', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/v1/cowork/chat/guest-session') && init?.method === 'POST') {
        return Promise.resolve(noContent());
      }
      if (url.endsWith('/v1/cowork/chat/projects')) {
        return Promise.resolve(response({ projects: [{
          project_id: 'project-default',
          name: 'Default Project',
          is_default: true,
          created_at: '2026-08-17T00:00:00Z',
        }] }));
      }
      if (url.includes('/sessions?project_id=project-default')) {
        return Promise.resolve(response({ sessions: [] }));
      }
      if (url.endsWith('/v1/cowork/chat/sessions') && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({ project_id: 'project-default' });
        return Promise.resolve(response({ session_id: 'session-default', project_id: 'project-default' }, 201));
      }
      if (url.endsWith('/sessions/session-default/messages') && init?.method === 'POST') {
        return Promise.resolve(sse([{ event_type: 'delta', text: 'Hello from the default project.' }, { event_type: 'completed' }]));
      }
      if (url.endsWith('/v1/cowork/chat/document-health')) {
        return Promise.resolve(response({ status: 'ready', checks: { feature: 'enabled' } }));
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    window.localStorage.clear();

    render(<Dashboard />);
    const input = screen.getByPlaceholderText('Tôi có thể giúp gì cho bạn hôm nay?');
    fireEvent.change(input, { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTitle('Gửi tin nhắn'));

    expect(await screen.findByText('Hello from the default project.')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      '/backend/v1/cowork/chat/guest-session',
      expect.objectContaining({ method: 'POST', credentials: 'include' })
    );
  });

  it('loads Project-scoped persisted chat history', async () => {
    vi.stubGlobal('fetch', projectFetch((url) => {
      if (url.includes('/sessions?project_id=project-default')) {
        return response({ sessions: [{ session_id: 'session-saved', project_id: 'project-default' }] });
      }
      if (url.endsWith('/sessions/session-saved/messages')) {
        return response({ turns: [{
          turn_id: 'turn-1',
          user_message: 'Saved question',
          assistant_message: 'Saved answer',
          created_at: '2026-08-12T00:00:00Z',
          citation_coordinates: [],
        }] });
      }
      return undefined;
    }));
    render(<Dashboard />);
    fireEvent.click((await screen.findAllByText('Default Project'))[0]);
    fireEvent.click((await screen.findAllByText('Chat 1'))[0]);
    expect(await screen.findByText('Saved question')).toBeTruthy();
    expect(screen.getByText('Saved answer')).toBeTruthy();
  });

  it('uploads composer files persistently to the active Project and opens the panel', async () => {
    const fetchMock = projectFetch((url, init) => {
      if (url.endsWith('/projects/project-default/documents') && init?.method === 'POST') {
        return response({ document_id: 'document-1', status: 'received' }, 202);
      }
      if (url.endsWith('/projects/project-default/documents')) {
        return response({ documents: [{
          document_id: 'document-1', project_id: 'project-default', title: 'policy.pdf',
          media_type: 'application/pdf', size_bytes: 9, status: 'received', reason_code: null,
          page_count: 0, chunk_count: 0, ocr_page_count: 0,
          expires_at: '2026-09-11T00:00:00Z',
        }] });
      }
      return undefined;
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<Dashboard />);
    await screen.findAllByText('Default Project');
    const file = new File(['%PDF-test'], 'policy.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByLabelText('Chọn tài liệu từ máy'), {
      target: { files: [file] },
    });
    expect(await screen.findByRole('dialog', { name: 'Project documents' })).toBeTruthy();
    expect((await screen.findAllByText('policy.pdf')).length).toBeGreaterThan(0);
    await waitFor(() => expect(
      fetchMock.mock.calls.some(([url, init]) =>
        String(url).endsWith('/projects/project-default/documents') &&
        (init as RequestInit | undefined)?.method === 'POST'
      )
    ).toBe(true));
  });

  it('hides document upload and the panel when user documents are disabled', async () => {
    const fetchMock = projectFetch((url) => {
      if (url.endsWith('/v1/cowork/chat/document-health')) {
        return response({ status: 'disabled', checks: { feature: 'disabled' } });
      }
      return undefined;
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<Dashboard />);

    await screen.findAllByText('Default Project');
    await waitFor(() => expect(
      screen.queryByLabelText('Chá»n tÃ i liá»‡u tá»« mÃ¡y')
    ).toBeNull());
    expect(screen.queryByRole('button', { name: 'Project documents' })).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) =>
      String(url).includes('/projects/project-default/documents')
    )).toBe(false);
  });

  it('keeps document upload available while document processing is degraded', async () => {
    const fetchMock = projectFetch((url) => {
      if (url.endsWith('/v1/cowork/chat/document-health')) {
        return response({ status: 'degraded', checks: { feature: 'enabled' } }, 503);
      }
      return undefined;
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<Dashboard />);

    await waitFor(() => expect(document.querySelector('input[type="file"]')).not.toBeNull());
    expect(screen.getByRole('button', { name: 'Project documents' })).toBeTruthy();
  });

  it('shows Project documents button in header on chat view and hides it on other views', async () => {
    vi.stubGlobal('fetch', projectFetch());
    render(<Dashboard />);

    await screen.findAllByText('Default Project');
    // On chat view, header button is present
    const docButton = screen.getByRole('button', { name: 'Project documents' });
    expect(docButton).toBeTruthy();

    // Clicking header button opens dialog
    fireEvent.click(docButton);
    expect(await screen.findByRole('dialog', { name: 'Project documents' })).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Close project documents'));

    // Switch to Mail view
    fireEvent.click(screen.getByTitle('Hộp thư'));
    expect(screen.queryByRole('button', { name: 'Project documents' })).toBeNull();
  });

  it('announces a background chat completion without changing the active view', async () => {
    vi.stubGlobal('fetch', projectFetch());
    render(<Dashboard />);
    await screen.findAllByText('Default Project');

    window.dispatchEvent(new CustomEvent('chat-background-completed', {
      detail: { sessionId: 'session-background', title: 'Quarterly plan' },
    }));

    expect((await screen.findByRole('status')).textContent).toContain(
      'Quarterly plan finished generating.'
    );
  });

  it('preserves active chat input and messages when selecting a project', async () => {
    vi.stubGlobal('fetch', projectFetch());
    render(<Dashboard />);
    await screen.findAllByText('Default Project');

    const input = screen.getByPlaceholderText('Tôi có thể giúp gì cho bạn hôm nay?');
    fireEvent.change(input, { target: { value: 'Tin nhắn đang soạn' } });
    expect((input as HTMLTextAreaElement).value).toBe('Tin nhắn đang soạn');

    // Click project item
    const projectBtn = (await screen.findAllByText('Default Project'))[0];
    fireEvent.click(projectBtn);

    // Input text should be preserved
    expect((screen.getByPlaceholderText('Tôi có thể giúp gì cho bạn hôm nay?') as HTMLTextAreaElement).value).toBe('Tin nhắn đang soạn');
  });
});

