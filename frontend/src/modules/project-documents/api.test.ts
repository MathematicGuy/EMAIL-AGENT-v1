import { afterEach, describe, expect, it, vi } from 'vitest';
import { waitForProjectDocument } from './api';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function processingDocument(): Response {
  return new Response(JSON.stringify({
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
  }), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

describe('project document polling', () => {
  it('stops polling after the configured timeout', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() =>
      Promise.resolve(processingDocument())
    ));
    const polling = waitForProjectDocument('project-1', 'document-1', {
      intervalMs: 1_000,
      timeoutMs: 2_500,
    });
    const rejected = expect(polling).rejects.toThrow('Document processing timed out.');

    await vi.advanceTimersByTimeAsync(2_500);
    await rejected;
  });

  it('cancels polling through AbortSignal', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() =>
      Promise.resolve(processingDocument())
    ));
    const controller = new AbortController();
    const polling = waitForProjectDocument('project-1', 'document-1', {
      intervalMs: 1_000,
      signal: controller.signal,
    });
    const rejected = expect(polling).rejects.toMatchObject({ name: 'AbortError' });

    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await rejected;
  });
});
