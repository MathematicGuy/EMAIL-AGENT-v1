import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Dashboard from './Dashboard';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('Dashboard Component', () => {
  it('renders correctly and allows typing and sending messages', async () => {
    vi.useFakeTimers();
    render(<Dashboard />);

    const input = screen.getByPlaceholderText('How can I help you today?');
    fireEvent.change(input, { target: { value: 'Xin chào AI' } });
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(screen.getByText('Xin chào AI')).toBeTruthy();
    expect(screen.getByText('F-Cowork AI')).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(3000);
    });
  });

  it('loads persisted chat history when clicking a recent conversation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((input: string | URL | Request) => {
        const url = String(input);
        if (url.endsWith('/api/v1/health')) {
          return Promise.resolve(new Response('{}', { status: 200 }));
        }
        if (url.includes('/conv-saved/messages?')) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                items: [
                  {
                    message_id: 'user-message',
                    role: 'user',
                    content: {
                      type: 'text',
                      text: 'Tin nhắn cũ từ backend',
                      metadata: {},
                    },
                    created_at: '2026-07-27T09:00:00Z',
                  },
                  {
                    message_id: 'assistant-message',
                    role: 'assistant',
                    content: {
                      type: 'text',
                      text: 'Phản hồi cũ từ backend',
                      metadata: {},
                    },
                    created_at: '2026-07-27T09:00:01Z',
                  },
                ],
              }),
              { status: 200, headers: { 'Content-Type': 'application/json' } }
            )
          );
        }
        if (url.includes('/v1/conversations?')) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                items: [
                  {
                    conversation_id: 'conv-saved',
                    title: 'Cuộc trò chuyện đã lưu',
                    message_count: 2,
                    last_activity_at: '2026-07-27T09:00:01Z',
                  },
                ],
              }),
              { status: 200, headers: { 'Content-Type': 'application/json' } }
            )
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      })
    );
    render(<Dashboard />);

    fireEvent.click(screen.getByTitle('Show sidebar (Click to expand)'));
    fireEvent.click(await screen.findByText('Cuộc trò chuyện đã lưu'));

    expect(await screen.findByText('Tin nhắn cũ từ backend')).toBeTruthy();
    expect(screen.getByText('Phản hồi cũ từ backend')).toBeTruthy();
  });

  it('prefills the landing idea prompt without sending it', () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith('/api/v1/health')) {
        return Promise.resolve(new Response('{}', { status: 200 }));
      }
      if (url.includes('/v1/conversations?')) {
        return Promise.resolve(
          new Response(JSON.stringify({ items: [] }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<Dashboard />);

    fireEvent.click(screen.getByText('Tạo báo cáo'));

    const input = screen.getByPlaceholderText(
      'How can I help you today?'
    ) as HTMLTextAreaElement;
    expect(input.value).toContain('báo cáo');
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).includes('/messages')
      )
    ).toHaveLength(0);
  });

  it('opens the dashboard utility panels from the header', () => {
    render(<Dashboard />);

    fireEvent.click(screen.getByTitle('Work intake'));
    expect(screen.getByRole('dialog', { name: 'Work intake' })).toBeTruthy();
    fireEvent.click(screen.getByLabelText('Close work intake panel'));

    fireEvent.click(screen.getByTitle('Memory & context'));
    expect(screen.getByRole('dialog', { name: 'Memory and context' })).toBeTruthy();
  });

  it('uploads a chat attachment and renders the returned artifact ref', async () => {
    const sourceRef = {
      ref_id: 'source-chat-1',
      checksum: 'sha256:source-chat-1',
      source_id: 'contract.md',
      media_type: 'text/markdown',
      size_bytes: 24,
      actor_id: 'demo-user',
      project_id: 'demo-project',
      workspace_id: 'demo-workspace',
      provenance: { upload_filename: 'contract.md' },
    };
    const artifactRef = {
      ...sourceRef,
      ref_id: 'artifact-chat-1',
      checksum: 'sha256:artifact-chat-1',
      source_id: 'Bao-cao.md',
      media_type: 'text/markdown',
      provenance: { upload_filename: 'Bao-cao.md' },
    };
    const fetchMock = vi.fn().mockImplementation(
      (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/api/v1/health')) {
          return Promise.resolve(new Response('{}', { status: 200 }));
        }
        if (url.includes('/v1/conversations?')) {
          return Promise.resolve(
            new Response(JSON.stringify({ items: [] }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            })
          );
        }
        if (
          url.endsWith('/v1/resources/uploads') &&
          init?.method === 'POST'
        ) {
          return Promise.resolve(
            new Response(JSON.stringify(sourceRef), {
              status: 201,
              headers: { 'Content-Type': 'application/json' },
            })
          );
        }
        if (
          url.endsWith('/v1/conversations') &&
          init?.method === 'POST'
        ) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                conversation_id: 'conv-chat-file',
                title: 'Tóm tắt file',
                message_count: 0,
                last_activity_at: '2026-07-28T04:00:00Z',
              }),
              {
                status: 201,
                headers: { 'Content-Type': 'application/json' },
              }
            )
          );
        }
        if (
          url.includes('/v1/conversations/conv-chat-file/messages') &&
          init?.method === 'POST'
        ) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                conversation_id: 'conv-chat-file',
                turn_id: 'turn-chat-file',
                events_url: '/events',
              }),
              {
                status: 202,
                headers: { 'Content-Type': 'application/json' },
              }
            )
          );
        }
        if (url.includes('/turns/turn-chat-file/events?')) {
          return Promise.resolve(
            new Response(
              [
                'id: 1',
                'event: task.created',
                'data: {"task_id":"task-chat-file","status":"RUNNING"}',
                '',
                'id: 2',
                'event: assistant.delta',
                'data: {"delta":"Đã tạo báo cáo từ file."}',
                '',
                'id: 3',
                'event: assistant.completed',
                `data: ${JSON.stringify({
                  task_id: 'task-chat-file',
                  artifact_refs: [artifactRef],
                  artifact_grounding: [
                    {
                      artifact_ref_id: 'artifact-chat-1',
                      grounding: {
                        status: 'GROUNDED',
                        label: 'Grounded Result',
                        source_coverage: 1,
                        sources: [
                          {
                            marker: 1,
                            kind: 'DOCUMENT',
                            reference_id: 'ev-sales',
                            display_name: 'sales.csv',
                            source_ref_id: 'source-chat-1',
                            locator: { row: 2 }
                          }
                        ]
                      }
                    }
                  ],
                })}`,
                '',
                '',
              ].join('\n'),
              {
                status: 200,
                headers: { 'Content-Type': 'text/event-stream' },
              }
            )
          );
        }
        if (url.includes('/v1/resources/artifact-chat-1?')) {
          return Promise.resolve(
            new Response(
              '# Báo cáo\n\nDoanh thu tăng 12%.[1]\n\n## Nguồn\n<a id="source-1"></a>[1] sales.csv',
              {
                status: 200,
                headers: { 'Content-Type': 'text/markdown' },
              }
            )
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${url}`));
      }
    );
    vi.stubGlobal('fetch', fetchMock);
    render(<Dashboard />);

    const file = new File(['# Contract\nPayment is due in 30 days.'], 'contract.md', {
      type: 'text/markdown',
    });
    fireEvent.change(screen.getByLabelText('Chọn tài liệu từ máy'), {
      target: { files: [file] },
    });
    expect(screen.getByText('contract.md')).toBeTruthy();

    fireEvent.change(
      screen.getByPlaceholderText('How can I help you today?'),
      { target: { value: 'Tạo báo cáo từ file này' } }
    );
    fireEvent.click(screen.getByTitle('Send message'));

    expect(await screen.findByText('Đã tạo báo cáo từ file.')).toBeTruthy();
    expect(screen.getByText('Đang phân tích yêu cầu')).toBeTruthy();
    fireEvent.click(screen.getByTitle('Mở rộng xem tại chỗ'));
    expect(await screen.findByText('Doanh thu tăng 12%.')).toBeTruthy();
    expect(screen.getByText(/Grounded Result/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Citation 1|Đi tới nguồn 1/i })).toBeTruthy();

    await waitFor(() => {
      const uploadCall = fetchMock.mock.calls.find(([url]) =>
        String(url).endsWith('/v1/resources/uploads')
      );
      expect(uploadCall).toBeTruthy();
      const messageCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url).includes('/conv-chat-file/messages') &&
          (init as RequestInit | undefined)?.method === 'POST'
      );
      const payload = JSON.parse(String(messageCall?.[1]?.body));
      expect(payload.content.attachment_refs[0].ref_id).toBe('source-chat-1');
    });
  });
});
