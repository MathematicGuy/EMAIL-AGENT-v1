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
    return Promise.resolve(response({
      connections: [connection],
      providerAvailability: {
        gmail: { enabled: true, reason: null },
        outlook: { enabled: true, reason: null },
      },
    }));
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
  window.localStorage.clear();
});

describe('MailInboxView', () => {
  it('opens directly to the latest action items without scan controls', async () => {
    const fetchMock = vi.fn(baseFetch);
    vi.stubGlobal('fetch', fetchMock);
    render(<MailInboxView />);

    expect(await screen.findByRole('option', { name: 'Gmail · owner@example.com' })).toBeTruthy();
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
                source_links: [
                  {
                    ref: 'link1',
                    label: 'Review document',
                    url: 'https://docs.example.com/review',
                  },
                  {
                    ref: 'link2',
                    label: null,
                    url: 'https://portal.example.com/open',
                  },
                ],
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
                source_links: [],
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
    await screen.findByRole('option', { name: 'Gmail · owner@example.com' });

    expect(await screen.findByText('Gửi báo cáo')).toBeTruthy();
    expect(
      screen.getByRole('button', { name: /Gửi báo cáo/ }).getAttribute('aria-expanded')
    ).toBe('true');
    expect(screen.getByText('Kiểm tra số liệu')).toBeTruthy();
    expect(screen.queryByText('Chuẩn bị giấy tờ')).toBeNull();

    const sourceLinks = screen.getByText('Source links (2)').closest('details');
    expect(sourceLinks).not.toBeNull();
    fireEvent.click(screen.getByText('Source links (2)'));
    expect(sourceLinks?.hasAttribute('open')).toBe(true);
    expect(screen.getByRole('link', { name: 'Review document' }).getAttribute('href')).toBe(
      'https://docs.example.com/review'
    );
    expect(
      screen.getByRole('link', { name: 'Open link — portal.example.com' }).getAttribute('href')
    ).toBe('https://portal.example.com/open');

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

  it('restores the provider selection and builds Outlook owner-bound connect URL', async () => {
    window.localStorage.setItem('cowork.mail.selected.outlook', 'outlook-1');
    vi.stubGlobal('fetch', vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.endsWith('/v1/mail-todo/connections')) return Promise.resolve(response({
        connections: [
          connection,
          { ...connection, id: 'outlook-1', provider: 'outlook', emailAddress: 'owner@outlook.com' },
        ],
        providerAvailability: {
          gmail: { enabled: true, reason: null },
          outlook: { enabled: true, reason: null },
        },
      }));
      if (url.includes('/v1/mail-todo/runs?')) return Promise.resolve(response({ runs: [] }));
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<MailInboxView />);

    const account = await screen.findByLabelText('Tài khoản email') as HTMLSelectElement;
    expect(account.value).toBe('outlook-1');
    expect(screen.getByRole('link', { name: 'Kết nối Outlook' }).getAttribute('href')).toContain(
      'ownerConnectionId=mbx-1'
    );
  });

  it('shows and clears an Outlook OAuth outcome marker', async () => {
    window.history.replaceState(null, '', '/?view=mail&outlook=connected');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ connections: [] })));
    render(<MailInboxView />);

    expect(await screen.findByText('Đã kết nối Outlook thành công.')).toBeTruthy();
    await waitFor(() => expect(window.location.search).not.toContain('outlook='));
  });

  it('renders quarantine security badge and opens warning modal on suspicious link click', async () => {
    const fetchMock = vi.fn().mockImplementation((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes('/v1/mail-todo/runs?')) {
        return Promise.resolve(
          response({
            runs: [{ id: 'run-1', status: 'succeeded', createdAt: '2026-08-10T00:00:00Z' }],
          })
        );
      }
      if (url.endsWith('/v1/mail-todo/runs/run-1/result')) {
        return Promise.resolve(
          response({
            run: {},
            actionItems: [{}],
            nextActions: [{}],
            attachmentWarnings: [],
            message: null,
          })
        );
      }
      if (url.endsWith('/v1/mail-todo/runs/run-1/tasks')) {
        return Promise.resolve(
          response({
            tasks: [
              {
                task_id: 'task-sec-1',
                run_id: 'run-1',
                gmail_message_id: 'message-phish',
                gmail_url: 'https://mail.google.com/mail/u/0/#inbox/message-phish',
                source_message_ids: ['message-phish'],
                source_links: [
                  {
                    ref: 'link-bad',
                    label: 'Fake Banking Login',
                    url: 'https://bank-login-fake.example.com/signin',
                    threat_level: 'malicious',
                  },
                ],
                incident_key: null,
                title: '[CẢNH BÁO BẢO MẬT] Phát hiện Email Phishing',
                request_summary: 'Email này đã bị cách ly.',
                actionability: 'actionable',
                route: 'no_action',
                priority: 'urgent',
                deadline: null,
                action_plan: [
                  { step: 1, instruction: 'Tuyệt đối không bấm link', supporting_citation_ids: [] },
                ],
                supporting_documents: [],
                missing_information: [],
                classifier_confidence: 1.0,
                generation_confidence: 1.0,
                validation_status: 'system_generated',
                created_at: '2026-08-10T00:00:00Z',
                quarantined: true,
                security_threat_level: 'malicious',
              },
            ],
          })
        );
      }
      return baseFetch(input);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<MailInboxView />);

    // Check quarantine badge is rendered
    expect(await screen.findByText(/Đã cách ly \(Mã độc \/ Phishing\)/)).toBeTruthy();
    expect(screen.getByText('[CẢNH BÁO BẢO MẬT] Phát hiện Email Phishing')).toBeTruthy();

    // Expand source links and click the malicious link
    fireEvent.click(screen.getByText('Source links (1)'));
    const badLinkBtn = screen.getByText('Fake Banking Login');
    expect(screen.getByText('NGUY HIỂM')).toBeTruthy();

    // Click link -> Should trigger warning modal instead of directly navigating
    fireEvent.click(badLinkBtn);

    expect(await screen.findByText('CẢNH BÁO BẢO MẬT: LIÊN KẾT NGUY HIỂM')).toBeTruthy();
    expect(screen.getByText('https://bank-login-fake.example.com/signin')).toBeTruthy();
    expect(screen.getByText('Quay lại an toàn')).toBeTruthy();

    // Dismiss modal
    fireEvent.click(screen.getByText('Quay lại an toàn'));
    expect(screen.queryByText('CẢNH BÁO BẢO MẬT: LIÊN KẾT NGUY HIỂM')).toBeNull();
  });
});
