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

function projectFetch(extra?: (url: string, init?: RequestInit) => Response | undefined) {
  return vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    const custom = extra?.(url, init);
    if (custom) return Promise.resolve(custom);
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
    const input = screen.getByPlaceholderText('How can I help you today?');
    fireEvent.change(input, { target: { value: 'Xin chào AI' } });
    expect((input as HTMLTextAreaElement).value).toBe('Xin chào AI');
    expect((await screen.findAllByText('Default Project')).length).toBeGreaterThan(0);
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
    fireEvent.click(screen.getByTitle('Show sidebar (Click to expand)'));
    fireEvent.click((await screen.findAllByText('Chat 1'))[0]);
    expect(await screen.findByText('Saved question')).toBeTruthy();
    expect(screen.getByText('Saved answer')).toBeTruthy();
  });

  it('opens the Work Intake utility panel', () => {
    vi.stubGlobal('fetch', projectFetch());
    render(<Dashboard />);
    fireEvent.click(screen.getByTitle('Work intake'));
    expect(screen.getByRole('dialog', { name: 'Work intake' })).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Close work intake panel'));
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
});
