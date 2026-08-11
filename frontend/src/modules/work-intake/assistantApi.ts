import { API_BASE_URL } from '../../lib/apiConfig';
import type { SourceSnapshotRef } from './types';

export interface AssistantScope {
  actorId: string;
  projectId: string;
  workspaceId: string;
}

export type AssistantModelId =
  | 'gemini-3.5-flash-lite'
  | 'gemini-3.6-flash-lite'
  | 'gemini-3.5-flash'
  | 'gemini-3.6-flash'
  | 'deepseek-openrouter'
  | 'deepseek-nvidia';

export const LOCAL_ASSISTANT_SCOPE: AssistantScope = {
  actorId: 'demo-user',
  projectId: 'demo-project',
  workspaceId: 'demo-workspace',
};

export interface ConversationView {
  conversation_id: string;
  title: string;
  message_count: number;
  last_activity_at: string;
  active_resources: SourceSnapshotRef[];
}

export interface ConversationMessage {
  message_id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: {
    type: 'text';
    text: string;
    attachment_refs: SourceSnapshotRef[];
    metadata: Record<string, unknown>;
  };
  created_at: string;
}

export interface TurnAccepted {
  conversation_id: string;
  turn_id: string;
  events_url: string;
}

interface TurnEventPage {
  items: BackendTurnEvent[];
}

interface BackendTurnEvent {
  sequence: number;
  event_type: string;
  data: Record<string, unknown>;
}

export interface TurnEvent {
  id: number;
  event: string;
  data: Record<string, unknown>;
}

export class AssistantApiError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
  }
}

function newId(prefix: string): string {
  const uuid =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}_${uuid}`;
}

function scopeParams(scope: AssistantScope): URLSearchParams {
  return new URLSearchParams({
    actor_id: scope.actorId,
    project_id: scope.projectId,
    workspace_id: scope.workspaceId,
  });
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
    const message =
      typeof payload === 'object' && payload !== null
        ? String(
            (payload as Record<string, unknown>).user_message ??
              (payload as Record<string, unknown>).detail ??
              `API trả về HTTP ${response.status}.`
          )
        : String(payload || `API trả về HTTP ${response.status}.`);
    throw new AssistantApiError(message, response.status);
  }

  return payload as T;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  try {
    const response = await fetch(url, init);
    return await parseResponse<T>(response);
  } catch (error) {
    if (error instanceof AssistantApiError) throw error;
    if ((error as { name?: string }).name === 'AbortError') throw error;
    throw new AssistantApiError(
      error instanceof Error ? error.message : 'Không thể kết nối tới backend local.'
    );
  }
}

export async function checkAssistantApi(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/health`, { signal });
    return response.ok;
  } catch {
    return false;
  }
}

export async function createConversation(
  scope: AssistantScope,
  signal?: AbortSignal
): Promise<string> {
  const conversation = await request<ConversationView>(
    `${API_BASE_URL}/v1/conversations`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        actor_id: scope.actorId,
        project_id: scope.projectId,
        workspace_id: scope.workspaceId,
      }),
      signal,
    }
  );
  return conversation.conversation_id;
}

export async function listConversations(
  scope: AssistantScope,
  signal?: AbortSignal
): Promise<ConversationView[]> {
  const response = await request<{ items: ConversationView[] }>(
    `${API_BASE_URL}/v1/conversations?${scopeParams(scope)}`,
    { signal }
  );
  return response.items;
}

export async function listConversationMessages(
  conversationId: string,
  scope: AssistantScope,
  signal?: AbortSignal
): Promise<ConversationMessage[]> {
  const response = await request<{ items: ConversationMessage[] }>(
    `${API_BASE_URL}/v1/conversations/${conversationId}/messages?${scopeParams(scope)}`,
    { signal }
  );
  return response.items;
}

export async function sendConversationMessage(input: {
  conversationId: string;
  text: string;
  modelId: AssistantModelId;
  attachmentRefs?: SourceSnapshotRef[];
  /** Answer to a clarification: resumes that turn instead of opening a new one. */
  replyToTurnId?: string;
  scope: AssistantScope;
  signal?: AbortSignal;
}): Promise<TurnAccepted> {
  return request<TurnAccepted>(
    `${API_BASE_URL}/v1/conversations/${input.conversationId}/messages`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': newId('chat'),
      },
      body: JSON.stringify({
        actor_id: input.scope.actorId,
        project_id: input.scope.projectId,
        workspace_id: input.scope.workspaceId,
        model_id: input.modelId,
        content: {
          type: 'text',
          text: input.text,
          attachment_refs: input.attachmentRefs ?? [],
          metadata: {},
        },
        ...(input.replyToTurnId ? { reply_to_turn_id: input.replyToTurnId } : {}),
        locale: 'vi-VN',
        timezone: 'Asia/Ho_Chi_Minh',
      }),
      signal: input.signal,
    }
  );
}

function parseSseBlock(block: string): TurnEvent | null {
  let id = 0;
  let event = 'message';
  const dataLines: string[] = [];

  for (const rawLine of block.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('id:')) {
      const parsed = Number.parseInt(line.slice(3).trim(), 10);
      if (Number.isFinite(parsed)) id = parsed;
    } else if (line.startsWith('event:')) {
      event = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) return null;

  try {
    return {
      id,
      event,
      data: JSON.parse(dataLines.join('\n')) as Record<string, unknown>,
    };
  } catch {
    return { id, event, data: { raw: dataLines.join('\n') } };
  }
}

export async function streamTurnEvents(input: {
  conversationId: string;
  turnId: string;
  /** Resume cursor; the reader owns it so a reconnect neither replays nor skips. */
  afterSequence?: number;
  scope: AssistantScope;
  signal?: AbortSignal;
  onEvent: (event: TurnEvent) => void;
}): Promise<void> {
  const cursor = String(input.afterSequence ?? 0);
  const params = scopeParams(input.scope);
  params.set('after_sequence', cursor);

  let response: Response;
  try {
    response = await fetch(
      `${API_BASE_URL}/v1/conversations/${input.conversationId}/turns/${input.turnId}/events?${params}`,
      {
        headers: { 'Last-Event-ID': cursor },
        signal: input.signal,
      }
    );
  } catch (error) {
    if ((error as { name?: string }).name === 'AbortError') throw error;
    throw new AssistantApiError(
      error instanceof Error ? error.message : 'Không thể mở luồng sự kiện.'
    );
  }

  if (!response.ok) {
    await parseResponse<never>(response);
  }

  if (response.headers.get('content-type')?.includes('application/json')) {
    const page = await parseResponse<TurnEventPage>(response);
    for (const event of page.items) {
      input.onEvent({
        id: event.sequence,
        event: event.event_type,
        data: event.data,
      });
    }
    return;
  }

  if (!response.body) {
    throw new AssistantApiError('Backend không trả về luồng sự kiện.', response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? '';

    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (event) input.onEvent(event);
    }

    if (done) break;
  }

  const finalEvent = parseSseBlock(buffer);
  if (finalEvent) input.onEvent(finalEvent);
}

export async function cancelTurn(input: {
  conversationId: string;
  turnId: string;
  scope: AssistantScope;
}): Promise<void> {
  const params = scopeParams(input.scope);
  await request(
    `${API_BASE_URL}/v1/conversations/${input.conversationId}/turns/${input.turnId}:cancel?${params}`,
    { method: 'POST' }
  );
}
