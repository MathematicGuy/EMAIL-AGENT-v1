import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import App from './App';

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function noContent(): Response {
  return new Response(null, { status: 204 });
}

function projectFetch() {
  return vi.fn().mockImplementation((input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/v1/cowork/chat/guest-session') && init?.method === 'POST') {
      return Promise.resolve(noContent());
    }
    if (url.endsWith('/v1/cowork/chat/projects')) {
      return Promise.resolve(
        response({
          projects: [
            {
              project_id: 'project-default',
              name: 'Default Project',
              is_default: true,
              created_at: '2026-08-12T00:00:00Z',
            },
          ],
        })
      );
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
    return Promise.resolve(response({}));
  });
}

describe('App Landing Page & Dashboard Navigation', () => {
  beforeEach(() => {
    window.location.hash = '';
    vi.stubGlobal('fetch', projectFetch());
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders landing page by default and navigates to dashboard on clicking CTA', async () => {
    window.location.hash = '';
    render(<App />);

    // Landing Page elements
    const ctaBtn = await screen.findByRole('button', { name: /Dùng Thử F-Cowork Miễn Phí/i });
    expect(ctaBtn).toBeTruthy();

    fireEvent.click(ctaBtn);

    // Dashboard elements should now be visible
    const input = await screen.findByPlaceholderText(
      'Tôi có thể giúp gì cho bạn hôm nay?',
      {},
      { timeout: 15000 }
    );
    expect(input).toBeTruthy();
  }, 20000);

  it('navigates back to landing page on clicking taskbar logo', async () => {
    window.location.hash = '#dashboard';
    render(<App />);

    // Initially on Dashboard (wait for lazy chunk)
    const input = await screen.findByPlaceholderText(
      'Tôi có thể giúp gì cho bạn hôm nay?',
      {},
      { timeout: 15000 }
    );
    expect(input).toBeTruthy();

    // Click logo in taskbar to go back to Landing Page
    const logoBtn = (await screen.findAllByTitle('Về trang chủ'))[0];
    fireEvent.click(logoBtn);

    const ctaBtn = await screen.findByRole('button', { name: /Dùng Thử F-Cowork Miễn Phí/i });
    expect(ctaBtn).toBeTruthy();
  }, 20000);
});
