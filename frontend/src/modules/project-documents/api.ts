import { API_BASE_URL } from '../../lib/apiConfig';

export type ProjectDocumentStatus =
  | 'received' | 'extracting' | 'indexing' | 'ready' | 'failed' | 'deleting' | 'deleted';

export interface ProjectDocument {
  document_id: string;
  project_id?: string;
  filename: string;
  media_type: string;
  byte_size: number;
  status: ProjectDocumentStatus;
  error_code: string | null;
  page_count: number;
  chunk_count: number;
  ocr_page_count: number;
  expires_at: string;
}

interface UploadInitiated {
  document_id: string;
  status: ProjectDocumentStatus;
  upload_url?: string;
}

interface DocumentHealth {
  checks?: { feature?: string };
  status?: string;
}

export interface ProjectDocumentPollingOptions {
  intervalMs?: number;
  timeoutMs?: number;
  signal?: AbortSignal;
}

const DEFAULT_POLL_INTERVAL_MS = 1_500;
export const DEFAULT_POLL_TIMEOUT_MS = 5 * 60 * 1_000;

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  let detail = `HTTP ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: string };
    detail = payload.detail ?? detail;
  } catch { /* keep status */ }
  throw new Error(detail);
}

function documentUrl(projectId: string, documentId?: string): string {
  const base = `${API_BASE_URL}/v1/cowork/chat/projects/${encodeURIComponent(projectId)}/documents`;
  return documentId ? `${base}/${encodeURIComponent(documentId)}` : base;
}

export async function areProjectDocumentsEnabled(): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/v1/cowork/chat/document-health`);
    const health = (await response.json()) as DocumentHealth;
    // The health endpoint returns 503 while optional document-processing
    // dependencies are warming up. That must not remove the upload controls:
    // the feature flag is the capability gate, while individual requests can
    // still surface an actionable error if a dependency remains unavailable.
    return health.checks?.feature === 'enabled';
  } catch {
    return false;
  }
}

async function sha256(file: File, signal?: AbortSignal): Promise<string> {
  signal?.throwIfAborted();
  const source = new Uint8Array(await file.arrayBuffer());
  const bytes = new Uint8Array(source.byteLength);
  bytes.set(source);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  signal?.throwIfAborted();
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function listProjectDocuments(
  projectId: string,
  signal?: AbortSignal
): Promise<ProjectDocument[]> {
  const response = await checked(await fetch(documentUrl(projectId), { signal }));
  return ((await response.json()) as { documents: ProjectDocument[] }).documents;
}

export async function getProjectDocument(
  projectId: string,
  documentId: string,
  signal?: AbortSignal
): Promise<ProjectDocument> {
  const response = await checked(await fetch(documentUrl(projectId, documentId), { signal }));
  return (await response.json()) as ProjectDocument;
}

export async function uploadProjectDocument(
  projectId: string,
  file: File,
  onStatus?: (status: 'hashing' | 'uploading' | 'processing') => void,
  signal?: AbortSignal
): Promise<ProjectDocument> {
  onStatus?.('hashing');
  const contentSha256 = await sha256(file, signal);
  const initiatedResponse = await checked(await fetch(documentUrl(projectId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      filename: file.name,
      media_type: file.type || mediaTypeFromName(file.name),
      byte_size: file.size,
      content_sha256: contentSha256,
    }),
    signal,
  }));
  const initiated = (await initiatedResponse.json()) as UploadInitiated;
  if (initiated.upload_url) {
    onStatus?.('uploading');
    await checked(await fetch(initiated.upload_url, {
      method: 'PUT',
      headers: { 'Content-Type': file.type || mediaTypeFromName(file.name) },
      body: file,
      signal,
    }));
    await checked(await fetch(`${documentUrl(projectId, initiated.document_id)}/complete`, {
      method: 'POST',
      signal,
    }));
  }
  onStatus?.('processing');
  return getProjectDocument(projectId, initiated.document_id, signal);
}

export async function waitForProjectDocument(
  projectId: string,
  documentId: string,
  options: ProjectDocumentPollingOptions = {}
): Promise<ProjectDocument> {
  const intervalMs = options.intervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const timeoutMs = options.timeoutMs ?? DEFAULT_POLL_TIMEOUT_MS;
  if (intervalMs < 1 || timeoutMs < 1) {
    throw new Error('Document polling interval and timeout must be positive.');
  }
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) abortFromCaller();
  else options.signal?.addEventListener('abort', abortFromCaller, { once: true });
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    for (;;) {
      const document = await getProjectDocument(projectId, documentId, controller.signal);
      if (['ready', 'failed', 'deleted'].includes(document.status)) return document;
      await abortableDelay(intervalMs, controller.signal);
    }
  } catch (cause) {
    if (timedOut) throw new Error('Document processing timed out.', { cause });
    throw cause;
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abortFromCaller);
  }
}

export async function deleteProjectDocument(
  projectId: string,
  documentId: string
): Promise<void> {
  await checked(await fetch(documentUrl(projectId, documentId), { method: 'DELETE' }));
}

function mediaTypeFromName(filename: string): string {
  return filename.toLowerCase().endsWith('.docx')
    ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    : 'application/pdf';
}

function abortableDelay(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
      return;
    }
    const abort = () => {
      window.clearTimeout(timer);
      reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, delayMs);
    signal.addEventListener('abort', abort, { once: true });
  });
}
