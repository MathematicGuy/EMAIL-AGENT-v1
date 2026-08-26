import { API_BASE_URL } from '../../lib/apiConfig';

export type DigestRunStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'failed';

export type MailProvider = 'gmail' | 'outlook';

export interface ProviderAvailability {
  enabled: boolean;
  reason: string | null;
}

export type ProviderAvailabilityMap = Record<MailProvider, ProviderAvailability>;

export interface MailboxConnectionsResponse {
  connections: MailboxConnection[];
  providerAvailability: ProviderAvailabilityMap;
}

export interface MailboxConnection {
  id: string;
  provider: MailProvider;
  emailAddress: string;
  scopes: string[];
  status: string;
  createdAt: string;
}

/** The per-user Google Calendar grant. Never carries the token itself. */
export interface CalendarConnection {
  id: string;
  provider: string;
  account: string;
  calendarId: string;
  timezone: string;
  status: string;
  connectedAt: string;
}

export interface UnreadPreviewMessage {
  messageId: string;
  threadId: string;
  subject: string;
  sender: string;
  receivedAt: string;
  attachmentsPresent: boolean;
  deepLink: string;
}

export interface UnreadPreview {
  emailsMatched: number;
  messages: UnreadPreviewMessage[];
  nextCursor: string | null;
}

export interface RunProgress {
  emailsMatched: number;
  emailsProcessed: number;
  emailsToProcess: number;
  maxEmails: number;
  actionItemsCount?: number;
  filteredSummary?: string | null;
}

export interface SafeRunError {
  code: string | null;
  message: string | null;
}

export interface DigestRunView {
  id: string;
  status: DigestRunStatus;
  progress: RunProgress;
  error: SafeRunError | null;
}

export interface DigestRunHistoryItem extends DigestRunView {
  mailboxConnectionId: string;
  createdAt: string | null;
  completedAt: string | null;
}

export interface DigestTaskPlanStep {
  step: number;
  instruction: string;
  supporting_citation_ids: string[];
}

export interface SupportingDocument {
  citation_id: string;
  document_id: string;
  title: string;
  section: string | null;
  url: string;
  relevance_score: number;
}

export interface EmailSourceLink {
  ref: string;
  label: string | null;
  url: string;
}

export interface DigestTask {
  task_id: string;
  run_id: string;
  gmail_message_id: string;
  gmail_url: string;
  source_message_ids: string[];
  source_links?: EmailSourceLink[];
  incident_key: string | null;
  title: string;
  request_summary: string;
  actionability: string;
  route: string;
  priority: string | null;
  deadline: string | null;
  action_plan: DigestTaskPlanStep[];
  supporting_documents: SupportingDocument[];
  missing_information: string[];
  classifier_confidence: number;
  generation_confidence: number | null;
  validation_status: string;
  created_at: string;
}

export interface AttachmentWarning {
  filename: string;
  code: string;
  message: string;
}

export interface DigestResult {
  run: Record<string, unknown>;
  actionItems: Array<Record<string, unknown>>;
  nextActions: Array<Record<string, unknown>>;
  attachmentWarnings: AttachmentWarning[];
  processedEmails?: Array<Record<string, unknown>>;
  message: string | null;
}

export class MailApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    const detail =
      typeof payload === 'object' && payload !== null
        ? (payload as Record<string, unknown>).detail
        : payload;
    throw new MailApiError(
      typeof detail === 'string' ? detail : `Mail API trả về HTTP ${response.status}.`,
      response.status
    );
  }
  return payload as T;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    return await parseResponse<T>(await fetch(`${API_BASE_URL}${path}`, init));
  } catch (error) {
    if (error instanceof MailApiError || (error as { name?: string }).name === 'AbortError') {
      throw error;
    }
    throw new MailApiError(
      error instanceof Error ? error.message : 'Không thể kết nối tới Mail API.',
      0
    );
  }
}

export function getGmailConnectUrl(): string {
  return `${API_BASE_URL}/v1/mail-todo/oauth/gmail/connect`;
}

export function getOutlookConnectUrl(ownerConnectionId: string): string {
  const params = new URLSearchParams({ ownerConnectionId });
  return `${API_BASE_URL}/v1/mail-todo/oauth/outlook/connect?${params}`;
}

export function getCalendarConnectUrl(): string {
  return `${API_BASE_URL}/v1/calendar/oauth/google/connect`;
}

export const MAILBOX_SELECTION_KEYS: Record<MailProvider, string> = {
  gmail: 'cowork.mail.selected.gmail',
  outlook: 'cowork.mail.selected.outlook',
};

export function getSelectedMailboxId(provider: MailProvider): string | null {
  return window.localStorage.getItem(MAILBOX_SELECTION_KEYS[provider]);
}

export function setSelectedMailboxId(provider: MailProvider, connectionId: string): void {
  window.localStorage.setItem(MAILBOX_SELECTION_KEYS[provider], connectionId);
}

export function newIdempotencyKey(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? `mail_${crypto.randomUUID()}`
    : `mail_${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
}

export async function listConnections(signal?: AbortSignal): Promise<MailboxConnectionsResponse> {
  const body = await request<{
    connections: MailboxConnection[];
    providerAvailability?: Partial<ProviderAvailabilityMap>;
  }>(
    '/v1/mail-todo/connections',
    { signal }
  );
  return {
    connections: body.connections,
    providerAvailability: {
      gmail: body.providerAvailability?.gmail ?? { enabled: true, reason: null },
      outlook: body.providerAvailability?.outlook ?? { enabled: false, reason: 'not_configured' },
    },
  };
}

/** The calendar half of the connect status. `null` means not connected.
 *
 * A 503 -- the deployment has no calendar handshake configured -- reads the
 * same as "not connected" on purpose: the user's answer to "is my calendar
 * working" is no either way, and a red error for a feature the server never
 * offered is noise.
 */
export async function readCalendarConnection(
  signal?: AbortSignal
): Promise<CalendarConnection | null> {
  try {
    const body = await request<{
      connected: boolean;
      connection: {
        id: string;
        provider: string;
        account: string;
        calendar_id: string;
        timezone: string;
        status: string;
        connected_at: string;
      } | null;
    }>('/v1/calendar/connection', { signal });
    if (!body.connected || body.connection === null) return null;
    return {
      id: body.connection.id,
      provider: body.connection.provider,
      account: body.connection.account,
      calendarId: body.connection.calendar_id,
      timezone: body.connection.timezone,
      status: body.connection.status,
      connectedAt: body.connection.connected_at,
    };
  } catch (error) {
    if (error instanceof MailApiError && (error.status === 401 || error.status === 503)) {
      return null;
    }
    throw error;
  }
}

export async function disconnectCalendar(): Promise<void> {
  await request<{ disconnected: boolean }>('/v1/calendar/connection', { method: 'DELETE' });
}

export async function disconnectConnection(connectionId: string): Promise<void> {
  await request<{ disconnected: boolean }>(
    `/v1/mail-todo/connections/${encodeURIComponent(connectionId)}`,
    { method: 'DELETE' }
  );
}

export function getUnreadPreview(
  connectionId: string,
  limit = 20,
  signal?: AbortSignal
): Promise<UnreadPreview> {
  return request(
    `/v1/mail-todo/connections/${encodeURIComponent(connectionId)}/unread-preview?limit=${limit}`,
    { signal }
  );
}

export async function createDigestRun(input: {
  mailboxConnectionId: string;
  maxEmails: number;
  idempotencyKey: string;
  query?: string;
  signal?: AbortSignal;
}): Promise<{ id: string; status: DigestRunStatus; statusUrl: string }> {
  return request('/v1/mail-todo/runs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': input.idempotencyKey,
    },
    body: JSON.stringify({
      mailboxConnectionId: input.mailboxConnectionId,
      maxEmails: input.maxEmails,
      ...(input.query ? { query: input.query } : {}),
    }),
    signal: input.signal,
  });
}

export function getDigestRun(runId: string, signal?: AbortSignal): Promise<DigestRunView> {
  return request(`/v1/mail-todo/runs/${encodeURIComponent(runId)}`, { signal });
}

export async function listDigestRuns(
  mailboxConnectionId: string,
  limit = 20,
  signal?: AbortSignal
): Promise<DigestRunHistoryItem[]> {
  const params = new URLSearchParams({ mailboxConnectionId, limit: String(limit) });
  const body = await request<{ runs: DigestRunHistoryItem[] }>(
    `/v1/mail-todo/runs?${params}`,
    { signal }
  );
  return body.runs;
}

export function getDigestResult(runId: string, signal?: AbortSignal): Promise<DigestResult> {
  return request(`/v1/mail-todo/runs/${encodeURIComponent(runId)}/result`, { signal });
}

export async function getDigestTasks(
  runId: string,
  signal?: AbortSignal
): Promise<DigestTask[]> {
  const body = await request<{ tasks: DigestTask[] }>(
    `/v1/mail-todo/runs/${encodeURIComponent(runId)}/tasks`,
    { signal }
  );
  return body.tasks;
}
