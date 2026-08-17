import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  areProjectDocumentsEnabled,
  uploadProjectDocument,
  waitForProjectDocument,
} from './api';

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

describe('project document availability and upload cancellation', () => {
  it('copies file bytes into a runtime-owned Uint8Array before hashing', async () => {
    const digest = vi.fn().mockImplementation((algorithm: string, data: BufferSource) => {
      expect(algorithm).toBe('SHA-256');
      expect(data).toBeInstanceOf(Uint8Array);
      expect(Object.getPrototypeOf(data).constructor).toBe(Uint8Array);
      expect((data as Uint8Array).buffer).toBeInstanceOf(ArrayBuffer);
      expect(Object.getPrototypeOf((data as Uint8Array).buffer).constructor).toBe(ArrayBuffer);
      return Promise.resolve(new Uint8Array(32).buffer);
    });
    vi.stubGlobal('crypto', { ...globalThis.crypto, subtle: { digest } });
    vi.stubGlobal('fetch', vi.fn().mockImplementation((_input: string | URL | Request, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return Promise.resolve(new Response(JSON.stringify({
          document_id: 'document-1', status: 'received',
        }), { status: 202, headers: { 'Content-Type': 'application/json' } }));
      }
      return Promise.resolve(new Response(JSON.stringify({
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
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }));

    await uploadProjectDocument(
      'project-1',
      new File(['%PDF-test'], 'policy.pdf', { type: 'application/pdf' }),
    );

    expect(digest).toHaveBeenCalledOnce();
  });

  it('keeps document controls fail-closed when feature is disabled', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: 'disabled',
      checks: { feature: 'disabled' },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    await expect(areProjectDocumentsEnabled()).resolves.toBe(false);
  });

  it('cancels before registering an upload when the caller aborts', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();
    controller.abort();

    await expect(uploadProjectDocument(
      'project-1',
      new File(['%PDF-test'], 'policy.pdf', { type: 'application/pdf' }),
      undefined,
      controller.signal,
    )).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
