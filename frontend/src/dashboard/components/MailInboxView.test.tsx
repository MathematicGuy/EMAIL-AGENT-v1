import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MailInboxView } from './MailInboxView';

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const connection = {
  id: 'mbx-1',
  provider: 'gmail',
  emailAddress: 'owner@example.com',
  scopes: ['https://www.googleapis.com/auth/gmail.readonly'],
  status: 'active',
  createdAt: '2026-08-10T00:00:00Z',
};

function baseFetch(input: string | URL | Request): Promise<Response> {
  const url = String(input);
  if (url.endsWith('/v1/mail-todo/connections')) {
    return Promise.resolve(response({ connections: [connection] }));
  }
  if (url.includes('/v1/mail-todo/runs?')) {
    return Promise.resolve(response({ runs: [] }));
  }
  throw new Error(`Unexpected request: ${url}`);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/');
});

describe('MailInboxView', () => {
  it('opens directly to the latest action items without scan controls', async () => {
    const fetchMock = vi.fn(baseFetch);
    vi.stubGlobal('fetch', fetchMock);
    render(<MailInboxView />);

    expect(await screen.findByText('owner@example.com')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Quét mail mới' })).toBeNull();
    expect(screen.queryByText('Chọn lịch sử quét')).toBeNull();
    expect(screen.getByText('Danh mục hành động (0)')).toBeTruthy();
    expect(screen.queryByText('Báo cáo tháng')).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/unread-preview'))).toBe(false);
  });

  it('shows the connect action when no mailbox exists', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ connections: [] })));
    render(<MailInboxView />);

    const connect = await screen.findByRole('link', { name: 'Kết nối Gmail' });
    expect(connect.getAttribute('href')).toContain('/v1/mail-todo/oauth/gmail/connect');
  });

  it('shows the latest Action Item plan and switches it on click', async () => {
    const fetchMock = vi.fn(
      async (input: string | URL | Request): Promise<Response> => {
        const url = String(input);
        if (url.includes('/v1/mail-todo/runs?')) {
          return response({
            runs: [{ id: 'run-1', status: 'succeeded', createdAt: '2026-08-10T00:00:00Z' }],
          });
        }
        if (url.endsWith('/v1/mail-todo/runs/run-1/result')) {
          return response({
            run: {},
            actionItems: [{}, {}],
            nextActions: [{}, {}],
            attachmentWarnings: [],
            message: null,
          });
        }
        if (url.endsWith('/v1/mail-todo/runs/run-1/tasks')) {
          return response({
            tasks: [
              {
                task_id: 'task-1',
                run_id: 'run-1',
                gmail_message_id: 'message-1',
                gmail_url: 'https://mail.google.com/mail/u/0/#inbox/message-1',
                source_message_ids: ['message-1'],
                incident_key: null,
                title: 'Gửi báo cáo',
                request_summary: 'Hoàn thiện báo cáo tháng.',
                actionability: 'actionable',
                route: 'direct_action',
                priority: 'high',
                deadline: null,
                action_plan: [
                  { step: 1, instruction: 'Kiểm tra số liệu', supporting_citation_ids: [] },
                ],
                supporting_documents: [],
                missing_information: [],
                classifier_confidence: 0.9,
                generation_confidence: 0.8,
                validation_status: 'system_generated',
                created_at: '2026-08-10T00:00:00Z',
              },
              {
                task_id: 'task-2',
                run_id: 'run-1',
                gmail_message_id: 'message-2',
                gmail_url: 'https://mail.google.com/mail/u/0/#inbox/message-2',
                source_message_ids: ['message-2'],
                incident_key: null,
                title: 'Nộp hồ sơ',
                request_summary: 'Nộp hồ sơ đăng ký đúng hạn.',
                actionability: 'actionable',
                route: 'retrieve_rag',
                priority: 'medium',
                deadline: null,
                action_plan: [
                  { step: 1, instruction: 'Chuẩn bị giấy tờ', supporting_citation_ids: [] },
                ],
                supporting_documents: [],
                missing_information: ['Quy trình nội bộ'],
                classifier_confidence: 0.9,
                generation_confidence: 0.8,
                validation_status: 'system_generated',
                created_at: '2026-08-10T00:00:00Z',
              },
            ],
          });
        }
        return baseFetch(input);
      }
    );
    vi.stubGlobal('fetch', fetchMock);
    render(<MailInboxView />);
    await screen.findByText('owner@example.com');

    expect(await screen.findByText('Gửi báo cáo')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /Gửi báo cáo/ }).getAttribute('aria-expanded')
    ).toBe('true');
    expect(screen.getByText('Kiểm tra số liệu')).toBeTruthy();
    expect(screen.queryByText('Chuẩn bị giấy tờ')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Nộp hồ sơ/ }));

    expect(await screen.findByText('Chuẩn bị giấy tờ')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /Nộp hồ sơ/ }).getAttribute('aria-expanded')
    ).toBe('true');
    expect(screen.queryByText('Kiểm tra số liệu')).toBeNull();
    expect(screen.getByText('Quy trình nội bộ')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Hoàn thành' })).toBeNull();
  });

  it('shows OAuth outcome and removes the transient marker from the URL', async () => {
    window.history.replaceState(
      null,
      '',
      '/?page=dashboard&view=mail&gmail=connected#dashboard'
    );
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ connections: [] })));
    render(<MailInboxView />);

    expect(await screen.findByText('Đã kết nối Gmail thành công.')).toBeTruthy();
    await waitFor(() => expect(window.location.search).not.toContain('gmail='));
  });
});
