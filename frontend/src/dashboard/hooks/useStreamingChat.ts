import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE_URL } from '../../lib/apiConfig';
import {
  uploadProjectDocument,
  waitForProjectDocument,
} from '../../modules/project-documents/api';
import {
  createDigestRun,
  getDigestRun,
  getDigestTasks,
  getSelectedMailboxId,
  listConnections,
  newIdempotencyKey,
  setSelectedMailboxId,
  type DigestRunView,
  type DigestTask,
  type MailProvider,
  type MailboxConnection,
} from '../../modules/mail/api';
import type { SourceSnapshotRef, StepView, TaskDetail } from '../../modules/work-intake/types';
import type {
  ChatCitation,
  ChatActivity,
  ChatActivityCode,
  ChatActivityOutcome,
  ChatActivityStatus,
  ChatComposerAttachment,
  ChatExecutionTrace,
  ChatGenerationStatus,
  ChatMessage,
  ChatRagEvidence,
  ChatRetrievalStatus,
  MailScanProgress,
  RecentChat,
  TaskWorkflow,
} from '../types';

interface ChatSession {
  session_id: string;
  project_id: string;
  title?: string;
  status?: string;
  latest_turn_status?: string;
}

interface ChatTurn {
  turn_id: string;
  user_message: string;
  assistant_message: string | null;
  created_at: string;
  citation_coordinates?: Array<Record<string, unknown>>;
  rag_evidence?: Array<Record<string, unknown>>;
  retrieval_status?: string;
  mail_scan?: Record<string, unknown>;
  status?: string;
  idempotency_key?: string;
  error_code?: string;
  activities?: unknown;
  completed_at?: string;
  execution_trace?: unknown;
  artifact_refs?: unknown;
}

interface SseEvent {
  event_type: string;
  text?: string;
  code?: string;
  safe_message?: string;
  source_id?: string;
  proposal?: Record<string, unknown>;
  citation_scope?: string;
  project_id?: string;
  document_id?: string;
  document_title?: string;
  section?: string;
  page_start?: number;
  page_end?: number;
  rag_evidence?: Array<Record<string, unknown>>;
  retrieval_status?: string;
  status?: string;
  idempotency_key?: string;
  turn_id?: string;
  error_code?: string;
  activities?: unknown;
  completed_at?: string;
  execution_trace?: unknown;
  artifact_refs?: unknown;
}

interface ChatRuntime {
  messages: ChatMessage[];
  draft: string;
  status: ChatGenerationStatus;
  selectedAttachments: ChatComposerAttachment[];
  attachmentError: string | null;
}

const TRANSCRIPT_CACHE_LIMIT = 20;
const NEW_CHAT_KEY = '__new_chat__';
const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;
const ACCEPTED_FILE_EXTENSIONS = new Set(['docx', 'pdf']);
const MAIL_COMMAND = /(?:^|\s)@(mail|email|outlook)\b/gi;
const MAIL_UNREAD_QUERY = 'is:unread in:inbox category:primary';
const MAIL_SCAN_MAX_EMAILS = 10;
const MAIL_POLL_INTERVAL_MS = 1_500;
const MAIL_TERMINAL_STATUSES = new Set(['succeeded', 'partial', 'failed']);
const ACTIVITY_CODES = new Set<ChatActivityCode>([
  'understanding_request', 'searching_relevant_information', 'reviewing_context',
  'preparing_response', 'preparing_action_plan', 'checking_mail', 'processing_email',
  'preparing_mail_results',
]);
const ACTIVITY_STATUSES = new Set<ChatActivityStatus>([
  'pending', 'running', 'completed', 'failed', 'cancelled', 'skipped',
]);
const ACTIVITY_OUTCOMES = new Set<ChatActivityOutcome>([
  'success', 'no_results', 'partial', 'degraded',
]);

export function validateAttachmentFile(file: File): string | null {
  if (file.size > MAX_ATTACHMENT_BYTES) return `${file.name} exceeds the 25 MiB limit.`;
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  if (!ACCEPTED_FILE_EXTENSIONS.has(extension)) {
    return `${file.name} must be a PDF or DOCX document.`;
  }
  return null;
}

function timestamp(value?: string): string {
  return new Date(value ?? Date.now()).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function mailCommandProviders(value: string): MailProvider[] {
  const providers = new Set<MailProvider>();
  for (const match of value.matchAll(MAIL_COMMAND)) {
    const command = match[1]?.toLowerCase();
    if (command === 'mail' || command === 'email') providers.add('gmail');
    if (command === 'mail' || command === 'outlook') providers.add('outlook');
  }
  return (['gmail', 'outlook'] as const).filter((provider) => providers.has(provider));
}

function isMailCommand(value: string): boolean {
  return mailCommandProviders(value).length > 0;
}

function mailScanProgress(run: DigestRunView): MailScanProgress {
  return {
    status: run.status,
    emailsMatched: run.progress.emailsMatched,
    emailsProcessed: run.progress.emailsProcessed,
    emailsToProcess: run.progress.emailsToProcess,
  };
}

function mailScanFromPayload(value: unknown): MailScanProgress | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const scan = value as Record<string, unknown>;
  if (
    !['connecting', 'queued', 'running', 'succeeded', 'partial', 'failed'].includes(scan.status as string) ||
    !Number.isInteger(scan.emails_matched) ||
    !Number.isInteger(scan.emails_processed) ||
    !Number.isInteger(scan.emails_to_process)
  ) return undefined;
  return {
    status: scan.status as MailScanProgress['status'],
    emailsMatched: scan.emails_matched as number,
    emailsProcessed: scan.emails_processed as number,
    emailsToProcess: scan.emails_to_process as number,
    actionItemsCount: Number.isInteger(scan.action_items_count)
      ? scan.action_items_count as number
      : undefined,
  };
}

function mailActivities(scan: MailScanProgress, previous: ChatActivity[] = []): ChatActivity[] {
  const now = new Date().toISOString();
  const old = new Map(previous.map((item) => [item.code, item]));
  const activity = (
    code: ChatActivityCode,
    status: ChatActivityStatus,
    outcome?: ChatActivityOutcome,
    detail?: ChatActivity['detail'],
  ): ChatActivity => {
    const prior = old.get(code);
    return {
      code,
      status,
      outcome,
      detail,
      startedAt: prior?.startedAt ?? (status === 'running' || status === 'completed' ? now : undefined),
      completedAt: prior?.completedAt ?? (status === 'completed' || status === 'failed' ? now : undefined),
    };
  };
  const processingDetail = {
    kind: 'emails_processed' as const, current: scan.emailsProcessed, total: scan.emailsToProcess,
  };
  if (scan.status === 'connecting') {
    return [
      activity('checking_mail', 'running'),
      activity('processing_email', 'pending'),
      activity('preparing_mail_results', 'pending'),
    ];
  }
  if (scan.status === 'queued' || scan.status === 'running') {
    return [
      activity('checking_mail', 'completed', 'success'),
      activity('processing_email', 'running', undefined, processingDetail),
      activity('preparing_mail_results', 'pending'),
    ];
  }
  const failed = scan.status === 'failed';
  return [
    activity('checking_mail', 'completed', 'success'),
    activity('processing_email', failed ? 'failed' : 'completed',
      failed ? undefined : scan.status === 'partial' ? 'partial' : 'success', processingDetail),
    activity('preparing_mail_results', failed ? 'skipped' : 'completed',
      failed ? undefined : scan.status === 'partial' ? 'partial' : 'success', {
        kind: 'action_items_prepared', current: scan.actionItemsCount ?? 0, total: scan.actionItemsCount ?? 0,
      }),
  ];
}

function activityWire(activities: ChatActivity[]): Array<Record<string, unknown>> {
  return activities.map((item) => ({
    code: item.code,
    status: item.status,
    ...(item.outcome ? { outcome: item.outcome } : {}),
    ...(item.detail ? { detail: item.detail } : {}),
  }));
}

function stopActivities(
  activities: ChatActivity[] | undefined,
  status: 'failed' | 'cancelled',
): ChatActivity[] | undefined {
  if (!activities?.length) return activities;
  const now = new Date().toISOString();
  return activities.map((item) => item.status === 'running'
    ? { ...item, status, completedAt: item.completedAt ?? now }
    : item.status === 'pending' ? { ...item, status: 'skipped' } : item);
}

function waitForMailPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, MAIL_POLL_INTERVAL_MS);
    signal.addEventListener('abort', () => {
      window.clearTimeout(timer);
      reject(new DOMException('Mail scan polling aborted', 'AbortError'));
    }, { once: true });
  });
}

function citationFromEvent(event: SseEvent): ChatCitation | null {
  if (
    event.citation_scope !== 'project_document' ||
    !event.source_id ||
    !event.project_id ||
    !event.document_id ||
    !event.document_title ||
    typeof event.page_start !== 'number' ||
    typeof event.page_end !== 'number'
  ) return null;
  return {
    citationId: event.source_id,
    projectId: event.project_id,
    documentId: event.document_id,
    documentTitle: event.document_title,
    section: event.section,
    pageStart: event.page_start,
    pageEnd: event.page_end,
  };
}

function citationFromCoordinate(value: Record<string, unknown>): ChatCitation | null {
  if (
    value.citation_scope !== 'project_document' ||
    typeof value.project_id !== 'string' ||
    typeof value.document_id !== 'string' ||
    typeof value.document_title !== 'string' ||
    typeof value.page_start !== 'number' ||
    typeof value.page_end !== 'number'
  ) return null;
  return {
    citationId: `${value.document_id}:${value.page_start}:${value.page_end}`,
    projectId: value.project_id,
    documentId: value.document_id,
    documentTitle: value.document_title,
    section: typeof value.section === 'string' ? value.section : undefined,
    pageStart: value.page_start,
    pageEnd: value.page_end,
    unavailable: value.unavailable === true,
  };
}

function retrievalStatus(value: unknown): ChatRetrievalStatus | undefined {
  return value === 'success' || value === 'no_results' || value === 'timeout' || value === 'unavailable'
    ? value
    : undefined;
}

function generationStatus(value: unknown): ChatGenerationStatus | undefined {
  const normalized = value === 'usage_limit' || value === 'usage_limited' ? 'usage_limit_reached'
    : value === 'rate_limited' ? 'temporarily_rate_limited'
      : value;
  return normalized === 'idle' || normalized === 'generating' || normalized === 'completed' ||
    normalized === 'failed' || normalized === 'interrupted' || normalized === 'cancelled' ||
    normalized === 'usage_limit_reached' || normalized === 'temporarily_rate_limited'
    ? normalized
    : undefined;
}

export function activitiesFromPayload(value: unknown): ChatActivity[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<ChatActivityCode>();
  return value.flatMap((item): ChatActivity[] => {
    if (!item || typeof item !== 'object') return [];
    const raw = item as Record<string, unknown>;
    if (!ACTIVITY_CODES.has(raw.code as ChatActivityCode) ||
        !ACTIVITY_STATUSES.has(raw.status as ChatActivityStatus) ||
        seen.has(raw.code as ChatActivityCode)) return [];
    const outcome = raw.outcome === null || raw.outcome === undefined
      ? undefined
      : ACTIVITY_OUTCOMES.has(raw.outcome as ChatActivityOutcome)
        ? raw.outcome as ChatActivityOutcome
        : undefined;
    let detail: ChatActivity['detail'];
    if (raw.detail && typeof raw.detail === 'object') {
      const value = raw.detail as Record<string, unknown>;
      if (['documents_found', 'emails_processed', 'action_items_prepared'].includes(value.kind as string) &&
          Number.isInteger(value.current) &&
          (value.total === null || value.total === undefined || Number.isInteger(value.total)) &&
          (value.current as number) >= 0 &&
          (value.total === null || value.total === undefined || (value.total as number) >= 0)) {
        detail = {
          kind: value.kind as NonNullable<ChatActivity['detail']>['kind'],
          current: value.current as number,
          total: typeof value.total === 'number' ? value.total : undefined,
        };
      }
    }
    const code = raw.code as ChatActivityCode;
    seen.add(code);
    return [{
      code,
      status: raw.status as ChatActivityStatus,
      outcome,
      startedAt: typeof raw.started_at === 'string' ? raw.started_at : undefined,
      completedAt: typeof raw.completed_at === 'string' ? raw.completed_at : undefined,
      detail,
    }];
  }).slice(0, 8);
}

function executionTraceFromPayload(value: unknown): ChatExecutionTrace | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const raw = value as Record<string, unknown>;
  if (typeof raw.provider !== 'string' || typeof raw.model !== 'string' ||
      (raw.mode !== 'fast' && raw.mode !== 'reasoning') ||
      !Array.isArray(raw.retrieved_filenames)) return undefined;
  const retrievedFilenames = raw.retrieved_filenames.filter(
    (item): item is string => typeof item === 'string',
  );
  if (retrievedFilenames.length !== raw.retrieved_filenames.length) return undefined;
  return {
    provider: raw.provider,
    model: raw.model,
    mode: raw.mode,
    reasoning: typeof raw.reasoning === 'string' ? raw.reasoning : undefined,
    reasoningTruncated: raw.reasoning_truncated === true,
    retrievedFilenames,
  };
}

function messagesFromTurns(turns: ChatTurn[]): ChatMessage[] {
  return turns.flatMap((turn) => {
    const citations = (turn.citation_coordinates ?? [])
      .map(citationFromCoordinate)
      .filter((item): item is ChatCitation => item !== null);
    const hasStoredEvidence = Array.isArray(turn.rag_evidence);
    const ragEvidence = hasStoredEvidence ? ragEvidenceFromPayload(turn.rag_evidence) : undefined;
    const status = retrievalStatus(turn.retrieval_status);
    const persistedGenerationStatus = generationStatus(turn.status);
    return [
      {
        id: `${turn.turn_id}-user`,
        role: 'user' as const,
        content: turn.user_message,
        timestamp: timestamp(turn.created_at),
      },
      {
        id: `${turn.turn_id}-assistant`,
        role: 'assistant' as const,
        content: turn.assistant_message ?? '',
        timestamp: timestamp(turn.created_at),
        citations,
        ragEvidence,
        retrievalStatus: status,
        mailScan: mailScanFromPayload(turn.mail_scan),
        generationStatus: (persistedGenerationStatus === 'generating'
          ? 'interrupted'
          : persistedGenerationStatus) ??
          (turn.assistant_message === null ? 'interrupted' : 'completed'),
        errorCode: turn.error_code,
        idempotencyKey: turn.idempotency_key,
        turnId: turn.turn_id,
        activities: activitiesFromPayload(turn.activities),
        completedAt: turn.completed_at,
        executionTrace: executionTraceFromPayload(turn.execution_trace),
        artifactRefs: artifactRefsFromPayload(turn.artifact_refs),
      },
    ];
  });
}

function artifactRefsFromPayload(value: unknown): SourceSnapshotRef[] | undefined {
  if (!Array.isArray(value) || value.length === 0) return undefined;
  return value.flatMap((ref) => {
    if (!ref || typeof ref !== 'object') return [];
    const obj = ref as Record<string, unknown>;
    const prov = (obj.provenance && typeof obj.provenance === 'object' ? obj.provenance : {}) as Record<string, unknown>;
    const refId = String(obj.ref_id || obj.filename || '');
    if (!refId) return [];
    return [{
      ref_id: refId,
      checksum: String(obj.checksum || ''),
      provenance: {
        upload_filename: String(prov.upload_filename || obj.filename || refId),
        title: typeof prov.title === 'string' ? prov.title : undefined,
      },
    }];
  });
}

function ragEvidenceFromPayload(value: unknown): ChatRagEvidence[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (
      !item || typeof item !== 'object' ||
      !['company_knowledge', 'project_document'].includes((item as Record<string, unknown>).source as string) ||
      (item as Record<string, unknown>).retrieval_status !== 'success' ||
      typeof (item as Record<string, unknown>).chunk_id !== 'string' ||
      typeof (item as Record<string, unknown>).document_id !== 'string' ||
      typeof (item as Record<string, unknown>).document_title !== 'string' ||
      typeof (item as Record<string, unknown>).relevance_score !== 'number' ||
      typeof (item as Record<string, unknown>).preview !== 'string'
    ) return [];
    const evidence = item as Record<string, unknown>;
    return [{
      source: evidence.source as ChatRagEvidence['source'],
      retrievalStatus: 'success' as const,
      chunkId: evidence.chunk_id as string,
      documentId: evidence.document_id as string,
      documentTitle: evidence.document_title as string,
      section: typeof evidence.section === 'string' ? evidence.section : null,
      sourceUrl: typeof evidence.source_url === 'string' ? evidence.source_url : null,
      relevanceScore: evidence.relevance_score as number,
      rerankScore: typeof evidence.rerank_score === 'number' ? evidence.rerank_score : null,
      preview: evidence.preview as string,
      content: typeof evidence.content === 'string' ? evidence.content : '',
    }];
  }).slice(0, 5);
}

function workflowFromTaskProposal(proposal: Record<string, unknown>): TaskWorkflow | null {
  if (
    typeof proposal.episode_id !== 'string' ||
    typeof proposal.task_title !== 'string' ||
    !Array.isArray(proposal.action_plan)
  ) {
    return null;
  }

  const episodeId = proposal.episode_id;
  const title = proposal.task_title;
  const actionPlan = proposal.action_plan.filter((item): item is string => typeof item === 'string');
  const validationStatus = typeof proposal.validation_status === 'string' ? proposal.validation_status : 'system_generated';
  const isApproved = validationStatus === 'user_approved' || validationStatus === 'completed';
  const nowIso = new Date().toISOString();

  const steps: StepView[] = actionPlan.map((desc, idx) => ({
    step_id: `step-${idx + 1}`,
    task_type: 'CHAT_ACTION',
    assigned_module: 'module_2',
    capability_id: desc,
    capability_version: '1.0',
    state: isApproved ? ('SUCCEEDED' as const) : ('READY' as const),
    required: true,
    allow_partial: false,
    accepted_partial: false,
    current_attempt: 1,
    current_job_id: null,
    operation_revision: 1,
    depends_on: [],
    completion_criteria: [],
    updated_at: nowIso,
  }));

  const detail: TaskDetail = {
    task: {
      schema_version: '1.0',
      task_id: episodeId,
      run_id: `run-${episodeId}`,
      request_id: `req-${episodeId}`,
      client_request_id: `client-${episodeId}`,
      status: isApproved ? 'COMPLETED' : 'AWAITING_PLAN_APPROVAL',
      state_version: 1,
      pause_requested: false,
      cancellation_generation: 0,
      current_plan_version: 1,
      actor_id: 'user',
      project_id: 'default',
      workspace_id: 'default',
      created_at: nowIso,
      updated_at: nowIso,
    },
    run: null,
    plan: {
      plan_id: `plan-${episodeId}`,
      plan_version: 1,
      objective: title,
      scope: [],
      out_of_scope: [],
      constraints: [],
      assumptions: [],
      risks: [],
      completion_criteria: [],
      approved: isApproved,
      steps,
      plan_steps: [],
    },
    steps,
  };

  return {
    taskId: episodeId,
    detail,
    events: [],
    phase: isApproved ? 'Kế hoạch đã được phê duyệt' : 'Chờ bạn phê duyệt kế hoạch',
    connectionState: 'live',
    lastEventSequence: 1,
  };
}

async function parseSse(
  response: Response,
  onEvent: (event: SseEvent) => void
): Promise<void> {
  if (!response.ok || !response.body) {
    let detail = `Chat request failed (HTTP ${response.status}).`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) detail = payload.detail;
    } catch { /* keep safe status message */ }
    throw new Error(detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split('\n\n');
    buffer = blocks.pop() ?? '';
    for (const block of blocks) {
      const data = block.split('\n').find((line) => line.startsWith('data:'));
      if (data) onEvent(JSON.parse(data.slice(5).trim()) as SseEvent);
    }
    if (done) break;
  }
}

export function useStreamingChat(
  modelId = 'gemini-3.5-flash-lite',
  projectId = '',
  projectIds: string[] = projectId ? [projectId] : [],
  reasoningMode: 'fast' | 'reasoning' = 'fast',
) {
  void modelId;
  void projectIds;
  const [recentChats, setRecentChats] = useState<RecentChat[]>([]);
  const [isSessionListLoading, setIsSessionListLoading] = useState(true);
  const [isTranscriptLoading, setIsTranscriptLoading] = useState(false);
  const [activeConversationId, setActiveConversationIdState] = useState<string | null>(null);
  const [draftKey, setDraftKey] = useState(NEW_CHAT_KEY);
  const [runtimeSnapshot, setRuntimeSnapshot] = useState(() => new Map<string, ChatRuntime>([[
    NEW_CHAT_KEY, {
      messages: [], draft: '', status: 'idle', selectedAttachments: [], attachmentError: null,
    },
  ]]));
  const [apiStatus, setApiStatus] = useState<'unknown' | 'online' | 'offline'>('unknown');
  const [workflows, setWorkflows] = useState<Record<string, TaskWorkflow>>({});
  const activeConversationRef = useRef<string | null>(null);
  const draftKeyRef = useRef(NEW_CHAT_KEY);
  const navigationEpochRef = useRef(0);
  const runtimesRef = useRef(runtimeSnapshot);
  const abortRefs = useRef(new Map<string, AbortController>());
  const cancelRequestedRefs = useRef(new Set<string>());
  const loadHistoryAbortRef = useRef<AbortController | null>(null);
  const transcriptCacheRef = useRef(new Map<string, ChatMessage[]>());
  const prefetchInFlightRef = useRef(new Set<string>());
  const attachmentPollsRef = useRef(new Map<string, AbortController>());
  const pendingProjectChatRef = useRef<{ projectId: string; sessionId: string } | null>(null);
  const mailPersistQueueRef = useRef(new Map<string, Promise<void>>());

  const runtimeFor = useCallback((key: string): ChatRuntime => {
    const existing = runtimesRef.current.get(key);
    if (existing) return existing;
    const created: ChatRuntime = {
      messages: [], draft: '', status: 'idle', selectedAttachments: [], attachmentError: null,
    };
    runtimesRef.current.set(key, created);
    return created;
  }, []);

  const updateRuntime = useCallback((
    key: string,
    update: (current: ChatRuntime) => ChatRuntime,
  ) => {
    runtimesRef.current.set(key, update(runtimeFor(key)));
    setRuntimeSnapshot(new Map(runtimesRef.current));
  }, [runtimeFor]);

  const activateConversation = useCallback((sessionId: string | null) => {
    activeConversationRef.current = sessionId;
    setActiveConversationIdState(sessionId);
  }, []);

  const activateNewDraft = useCallback(() => {
    const existing = runtimesRef.current.get(draftKeyRef.current);
    const key = existing?.status === 'idle'
      ? draftKeyRef.current
      : `${NEW_CHAT_KEY}:${navigationEpochRef.current}`;
    draftKeyRef.current = key;
    setDraftKey(key);
    activateConversation(null);
  }, [activateConversation]);

  const activeRuntime = runtimeSnapshot.get(activeConversationId ?? draftKey) ?? {
    messages: [], draft: '', status: 'idle' as const, selectedAttachments: [], attachmentError: null,
  };
  const messages = activeRuntime.messages;
  const inputText = activeRuntime.draft;
  const isGenerating = activeRuntime.status === 'generating';
  const selectedAttachments = activeRuntime.selectedAttachments;
  const attachmentError = activeRuntime.attachmentError;

  const setInputText = useCallback((value: string | ((current: string) => string)) => {
    const key = activeConversationRef.current ?? draftKeyRef.current;
    updateRuntime(key, (current) => ({
      ...current,
      draft: typeof value === 'function' ? value(current.draft) : value,
    }));
  }, [updateRuntime]);

  const rememberTranscript = useCallback((sessionId: string, next: ChatMessage[]) => {
    const cache = transcriptCacheRef.current;
    cache.delete(sessionId);
    cache.set(sessionId, next);
    while (cache.size > TRANSCRIPT_CACHE_LIMIT) {
      const oldest = cache.keys().next().value;
      if (oldest === undefined) break;
      cache.delete(oldest);
    }
  }, []);

  const refreshHistory = useCallback(async () => {
    if (!projectId) {
      setRecentChats([]);
      setIsSessionListLoading(false);
      return;
    }
    setIsSessionListLoading(true);
    try {
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions?project_id=${encodeURIComponent(projectId)}`,
        { credentials: 'include' }
      );
      if (response.status === 401) {
        setApiStatus('online');
        setRecentChats([]);
        return;
      }
      if (!response.ok) throw new Error();
      const payload = (await response.json()) as { sessions: ChatSession[] };
      setRecentChats((current) => {
        const localById = new Map(current.map((chat) => [chat.id, chat]));
        const serverIds = new Set(payload.sessions.map((session) => session.session_id));
        const reversedSessions = [...payload.sessions].reverse();
        const serverChats: RecentChat[] = reversedSessions.map((session, index) => {
          const local = localById.get(session.session_id);
          const persistedStatus = generationStatus(session.latest_turn_status ?? session.status);
          return {
            id: session.session_id,
            title: session.title ?? local?.title ?? `Chat ${payload.sessions.length - index}`,
            projectId: session.project_id,
            category: 'recent',
            unread: local?.unread,
            generationStatus: runtimesRef.current.get(session.session_id)?.status ??
              (persistedStatus === 'generating' ? 'interrupted' : persistedStatus) ??
              local?.generationStatus,
          };
        });
        const runtimeBacked = current.filter((chat) =>
          chat.projectId === projectId &&
          !serverIds.has(chat.id) &&
          runtimesRef.current.has(chat.id)
        );
        return [...runtimeBacked, ...serverChats];
      });
      setApiStatus('online');
    } catch {
      setApiStatus('offline');
    } finally {
      setIsSessionListLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    queueMicrotask(() => {
      const pending = pendingProjectChatRef.current;
      if (pending?.projectId === projectId) {
        pendingProjectChatRef.current = null;
        activateConversation(pending.sessionId);
      } else if (!activeConversationRef.current && (runtimesRef.current.get(draftKeyRef.current)?.messages.length ?? 0) === 0) {
        activateNewDraft();
      }
      void refreshHistory();
    });
  }, [activateConversation, activateNewDraft, projectId, refreshHistory]);

  useEffect(() => () => {
    for (const controller of attachmentPollsRef.current.values()) controller.abort();
    attachmentPollsRef.current.clear();
  }, []);

  const ensureSession = useCallback(async (projectIdOverride?: string): Promise<string> => {
    if (activeConversationId) return activeConversationId;
    const sessionProjectId = projectIdOverride ?? projectId;
    if (!sessionProjectId) throw new Error('Select a Project before starting chat.');
    const response = await fetch(`${API_BASE_URL}/v1/cowork/chat/sessions`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: sessionProjectId }),
    });
    if (!response.ok) throw new Error(`Could not create chat session (HTTP ${response.status}).`);
    const payload = (await response.json()) as ChatSession;
    return payload.session_id;
  }, [activeConversationId, projectId]);

  const selectAttachments = useCallback((files: File[]) => {
    const runtimeKey = activeConversationRef.current ?? draftKeyRef.current;
    const updateAttachments = (
      update: (current: ChatComposerAttachment[]) => ChatComposerAttachment[],
    ) => updateRuntime(runtimeKey, (current) => ({
      ...current, selectedAttachments: update(current.selectedAttachments),
    }));
    const updateError = (attachmentError: string | null) => updateRuntime(
      runtimeKey, (current) => ({ ...current, attachmentError }),
    );
    if (!projectId) {
      updateError('Select a Project before uploading documents.');
      return;
    }
    window.dispatchEvent(new CustomEvent('open-project-documents'));
    for (const file of files) {
      const validation = validateAttachmentFile(file);
      const id = `upload_${crypto.randomUUID?.() ?? Date.now()}`;
      if (validation) {
        updateError(validation);
        continue;
      }
      const pollController = new AbortController();
      attachmentPollsRef.current.set(id, pollController);
      updateAttachments((current) => [...current, {
        id,
        name: file.name,
        mediaType: file.type,
        sizeBytes: file.size,
        status: 'hashing',
      }]);
      void uploadProjectDocument(projectId, file, (status) => {
        if (pollController.signal.aborted) return;
        updateAttachments((current) => current.map((item) =>
          item.id === id ? { ...item, status } : item
        ));
      }, pollController.signal).then(async (document) => {
        if (pollController.signal.aborted) return;
        updateAttachments((current) => current.map((item) =>
          item.id === id ? {
            ...item,
            documentId: document.document_id,
            status: document.status === 'ready' ? 'ready' : 'processing',
          } : item
        ));
        const finished = document.status === 'ready' || document.status === 'failed'
          ? document
          : await waitForProjectDocument(projectId, document.document_id, {
            signal: pollController.signal,
          });
        updateAttachments((current) => current.map((item) =>
          item.id === id ? {
            ...item,
            status: finished.status === 'ready' ? 'ready' : 'error',
            error: finished.status === 'failed'
              ? (finished.error_code ?? 'Document processing failed.')
              : undefined,
          } : item
        ));
        updateError(null);
        window.dispatchEvent(new CustomEvent('project-documents-updated'));
      }).catch((cause) => {
        if (pollController.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : 'Upload failed.';
        updateAttachments((current) => current.map((item) =>
          item.id === id ? { ...item, status: 'error', error: message } : item
        ));
        updateError(message);
      }).finally(() => {
        if (attachmentPollsRef.current.get(id) === pollController) {
          attachmentPollsRef.current.delete(id);
        }
      });
    }
  }, [projectId, updateRuntime]);

  const removeAttachment = useCallback((attachmentId: string) => {
    const runtimeKey = activeConversationRef.current ?? draftKeyRef.current;
    attachmentPollsRef.current.get(attachmentId)?.abort();
    attachmentPollsRef.current.delete(attachmentId);
    updateRuntime(runtimeKey, (current) => ({
      ...current,
      selectedAttachments: current.selectedAttachments.filter((item) => item.id !== attachmentId),
    }));
  }, [updateRuntime]);

  const runMailScan = useCallback(async (
    sessionId: string,
    assistantId: string,
    abort: AbortController,
    providers: MailProvider[],
    onProgress?: (content: string, scan: MailScanProgress, activities: ChatActivity[]) => void,
  ): Promise<{ content: string; mailScan: MailScanProgress }> => {
    const updateAssistant = (
      content: string,
      mailScan: MailScanProgress,
      isStreaming = true,
    ) => {
      updateRuntime(sessionId, (current) => ({
        ...current,
        messages: current.messages.map((message) =>
          message.id === assistantId ? {
            ...message,
            content,
            mailScan,
            activities: mailActivities(mailScan, message.activities),
            isStreaming,
          } : message
        ),
      }));
      const currentMessage = runtimeFor(sessionId).messages.find((message) => message.id === assistantId);
      onProgress?.(content, mailScan, mailActivities(mailScan, currentMessage?.activities));
    };
    interface ProviderOutcome {
      provider: MailProvider;
      content: string;
      progress: MailScanProgress;
    }
    const label = (provider: MailProvider) => provider === 'gmail' ? 'Gmail' : 'Outlook';
    const states = new Map<MailProvider, ProviderOutcome>();
    const aggregate = (): MailScanProgress => {
      const values = providers.map((provider) => states.get(provider)).filter(
        (value): value is ProviderOutcome => Boolean(value)
      );
      const terminal = values.length === providers.length && values.every(
        (value) => MAIL_TERMINAL_STATUSES.has(value.progress.status)
      );
      let status: MailScanProgress['status'];
      if (terminal) {
        const usable = values.filter((value) =>
          value.progress.status === 'succeeded' || value.progress.status === 'partial'
        );
        status = usable.length === 0
          ? 'failed'
          : values.every((value) => value.progress.status === 'succeeded') ? 'succeeded' : 'partial';
      } else if (values.some((value) => value.progress.status === 'running')) {
        status = 'running';
      } else if (values.some((value) => value.progress.status === 'queued')) {
        status = 'queued';
      } else {
        status = 'connecting';
      }
      return {
        status,
        emailsMatched: values.reduce((sum, value) => sum + value.progress.emailsMatched, 0),
        emailsProcessed: values.reduce((sum, value) => sum + value.progress.emailsProcessed, 0),
        emailsToProcess: values.reduce((sum, value) => sum + value.progress.emailsToProcess, 0),
        actionItemsCount: values.reduce(
          (sum, value) => sum + (value.progress.actionItemsCount ?? 0), 0
        ),
      };
    };
    const publish = (provider: MailProvider, outcome: ProviderOutcome) => {
      states.set(provider, outcome);
      const lines = providers.map((item) => {
        const state = states.get(item);
        return `${label(item)}: ${state?.content ?? 'Đang chuẩn bị…'}`;
      });
      const progress = aggregate();
      updateAssistant(
        lines.join('\n'),
        progress,
        !MAIL_TERMINAL_STATUSES.has(progress.status)
      );
    };
    const listed = await listConnections(abort.signal);
    const activeConnections = listed.connections.filter(
      (connection) => connection.status === 'active'
    );
    const selectedConnection = (provider: MailProvider): MailboxConnection | undefined => {
      const candidates = activeConnections.filter((connection) => connection.provider === provider);
      const remembered = getSelectedMailboxId(provider);
      const selected = candidates.find((connection) => connection.id === remembered) ?? candidates[0];
      if (selected) setSelectedMailboxId(provider, selected.id);
      return selected;
    };
    const scanProvider = async (provider: MailProvider): Promise<ProviderOutcome> => {
      const connection = selectedConnection(provider);
      if (!connection) {
        return {
          provider,
          content: `Chưa có tài khoản ${label(provider)} đang kết nối. Hãy mở Mail Inbox để kết nối ${label(provider)}.`,
          progress: { status: 'failed', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0 },
        };
      }
      publish(provider, {
        provider,
        content: 'Đang tạo lượt quét 10 email unread mới nhất…',
        progress: { status: 'connecting', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0 },
      });
      try {
        const accepted = await createDigestRun({
          mailboxConnectionId: connection.id,
          maxEmails: MAIL_SCAN_MAX_EMAILS,
          query: provider === 'gmail' ? MAIL_UNREAD_QUERY : undefined,
          idempotencyKey: newIdempotencyKey(),
          signal: abort.signal,
        });
        let consecutiveErrors = 0;
        while (!abort.signal.aborted) {
          let run: DigestRunView;
          try {
            run = await getDigestRun(accepted.id, abort.signal);
            consecutiveErrors = 0;
          } catch (err) {
            if ((err as { name?: string }).name === 'AbortError') throw err;
            consecutiveErrors++;
            if (consecutiveErrors >= 5) throw err;
            await waitForMailPoll(abort.signal);
            continue;
          }
          const progress = mailScanProgress(run);
          if (!MAIL_TERMINAL_STATUSES.has(run.status)) {
            publish(provider, { provider, content: 'Đang quét email unread mới nhất…', progress });
            await waitForMailPoll(abort.signal);
            continue;
          }
          if (run.status === 'failed') {
            return {
              provider,
              content: run.error?.message ?? 'Không thể hoàn tất lượt quét email.',
              progress,
            };
          }
          let tasks: DigestTask[] = [];
          try {
            tasks = await getDigestTasks(run.id, abort.signal);
          } catch (err) {
            if ((err as { name?: string }).name === 'AbortError') throw err;
          }
          const finalCount = tasks.length || run.progress.actionItemsCount || 0;
          const resultLabel = run.status === 'partial' ? 'Hoàn tất một phần' : 'Đã quét xong';
          return {
            provider,
            content: [
              `${resultLabel}: đã quét ${run.progress.emailsProcessed} email và tạo ${finalCount} công việc.`,
              run.progress.filteredSummary?.trim(),
            ].filter(Boolean).join(' '),
            progress: { ...progress, actionItemsCount: finalCount },
          };
        }
        throw new DOMException('Mail scan polling aborted', 'AbortError');
      } catch (error) {
        if ((error as { name?: string }).name === 'AbortError') throw error;
        return {
          provider,
          content: error instanceof Error ? error.message : 'Không thể hoàn tất lượt quét email.',
          progress: { status: 'failed', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0 },
        };
      }
    };
    const outcomes = await Promise.all(providers.map(async (provider) => {
      const outcome = await scanProvider(provider);
      publish(provider, outcome);
      return outcome;
    }));
    const mailScan = aggregate();
    const content = outcomes.map((outcome) => `${label(outcome.provider)}: ${outcome.content}`).join('\n');
    updateAssistant(content, mailScan, false);
    return { content, mailScan };
  }, [runtimeFor, updateRuntime]);

  const persistMailScanTurn = useCallback(async (
    sessionId: string,
    turnId: string,
    userMessage: string,
    assistantMessage: string | null,
    mailScan: MailScanProgress,
    signal: AbortSignal,
    options?: {
      idempotencyKey?: string;
      turnStatus?: ChatGenerationStatus;
      activities?: ChatActivity[];
    },
  ) => {
    let targetSessionId = sessionId;
    let response = await fetch(
      `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(targetSessionId)}/mail-scans`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          turn_id: turnId,
          idempotency_key: options?.idempotencyKey,
          user_message: userMessage,
          assistant_message: assistantMessage,
          turn_status: options?.turnStatus,
          mail_scan: {
            status: mailScan.status,
            emails_matched: mailScan.emailsMatched,
            emails_processed: mailScan.emailsProcessed,
            emails_to_process: mailScan.emailsToProcess,
            action_items_count: mailScan.actionItemsCount ?? null,
          },
          activities: activityWire(options?.activities ?? []),
        }),
        signal,
      }
    );
    if (response.status === 404 && projectId) {
      try {
        const createRes = await fetch(`${API_BASE_URL}/v1/cowork/chat/sessions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: projectId }),
          signal,
        });
        if (createRes.ok) {
          const newSession = (await createRes.json()) as ChatSession;
          targetSessionId = newSession.session_id;
          activateConversation(newSession.session_id);
          response = await fetch(
            `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(targetSessionId)}/mail-scans`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                turn_id: turnId,
                idempotency_key: options?.idempotencyKey,
                user_message: userMessage,
                assistant_message: assistantMessage,
                turn_status: options?.turnStatus,
                mail_scan: {
                  status: mailScan.status,
                  emails_matched: mailScan.emailsMatched,
                  emails_processed: mailScan.emailsProcessed,
                  emails_to_process: mailScan.emailsToProcess,
                  action_items_count: mailScan.actionItemsCount ?? null,
                },
                activities: activityWire(options?.activities ?? []),
              }),
              signal,
            }
          );
        }
      } catch {
        // Ignore fallback failure
      }
    }
    if (!response.ok) {
      console.warn(`Could not save mail scan (HTTP ${response.status}).`);
      return;
    }
    try {
      const turn = await response.json() as ChatTurn;
      const canonicalActivities = activitiesFromPayload(turn.activities);
      updateRuntime(targetSessionId, (current) => ({ ...current,
        messages: current.messages.map((message) => message.id === turnId ? {
          ...message,
          ...(canonicalActivities.length > 0 ? { activities: canonicalActivities } : {}),
          completedAt: turn.completed_at ?? message.completedAt,
          turnId: turn.turn_id ?? message.turnId,
        } : message),
      }));
    } catch {
      // Older compatible responses may not return the canonical turn body.
    }
  }, [activateConversation, projectId, updateRuntime]);

  const setChatStatus = useCallback((
    sessionId: string,
    status: ChatGenerationStatus,
    unread = false,
  ) => {
    updateRuntime(sessionId, (current) => ({ ...current, status }));
    setRecentChats((current) => current.map((chat) => chat.id === sessionId
      ? { ...chat, generationStatus: status, unread: unread || chat.unread }
      : chat));
  }, [updateRuntime]);

  const requestTurnCancellation = useCallback(async (sessionId: string) => {
    const assistant = runtimeFor(sessionId).messages.findLast(
      (message) => message.role === 'assistant' && message.isStreaming
    );
    if (!assistant) return;
    const turnPath = assistant.turnId
      ? `/turns/${encodeURIComponent(assistant.turnId)}/cancel`
      : '/turns/cancel';
    await fetch(
      `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}${turnPath}`,
      {
        method: 'POST',
        credentials: 'include',
        headers: assistant.turnId ? undefined : { 'Content-Type': 'application/json' },
        body: assistant.turnId ? undefined : JSON.stringify({
          idempotency_key: assistant.idempotencyKey,
        }),
      },
    );
  }, [runtimeFor]);

  const sendMessage = useCallback(async (
    override?: string,
    projectIdOverride?: string,
    retry?: { assistantId: string; idempotencyKey: string },
  ) => {
    const originSessionId = activeConversationRef.current;
    const originKey = originSessionId ?? draftKeyRef.current;
    const originRuntime = runtimeFor(originKey);
    const text = (override ?? originRuntime.draft).trim();
    if (!text || originRuntime.status === 'generating') return;
    if (selectedAttachments.some((item) => item.status !== 'ready')) {
      const message = navigator.language.toLowerCase().startsWith('vi')
          ? 'Tài liệu đang được xử lý. Hãy chờ hoặc gỡ tài liệu để gửi tin nhắn.'
          : 'A selected document is still processing. Wait or remove it before sending.';
      updateRuntime(originKey, (current) => ({ ...current, attachmentError: message }));
      return;
    }
    const now = Date.now();
    const mailRequest = isMailCommand(text);
    const navigationEpoch = navigationEpochRef.current;
    let sessionId: string | null = originSessionId;
    const assistantId = retry?.assistantId ?? `assistant-${now}`;
    const idempotencyKey = retry?.idempotencyKey ?? `turn_${crypto.randomUUID?.() ?? now}`;
    const abort = new AbortController();
    let mailSessionId: string | null = null;
    const userMessage: ChatMessage = {
      id: `user-${now}`,
      role: 'user',
      content: text,
      timestamp: timestamp(),
    };
    updateRuntime(originKey, (current) => {
      const messages = retry
        ? current.messages.map((message) => message.id === assistantId
          ? {
              ...message, content: '', isStreaming: true, generationStatus: 'generating' as const,
              errorCode: undefined, idempotencyKey,
              activities: mailRequest
                ? mailActivities({ status: 'connecting', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0 })
                : message.activities,
            }
          : message)
        : [...current.messages, userMessage, {
            id: assistantId,
            role: 'assistant' as const,
            content: '',
            timestamp: timestamp(),
            isStreaming: true,
            generationStatus: 'generating' as const,
            idempotencyKey,
            activities: mailRequest
              ? mailActivities({ status: 'connecting', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0 })
              : undefined,
          }];
      return { ...current, messages, draft: '', status: 'generating' };
    });
    abortRefs.current.set(originKey, abort);
    const sessionProjectId = projectIdOverride ?? projectId;
    const temporaryTitle = text.length > 48 ? `${text.slice(0, 47)}…` : text;
    setRecentChats((current) => [{
      id: originKey,
      title: temporaryTitle,
      projectId: sessionProjectId,
      category: 'recent',
      generationStatus: 'generating',
    }, ...current.filter((chat) => chat.id !== originKey)]);
    try {
      sessionId = await ensureSession(projectIdOverride);
      if (!originSessionId) {
        const provisional = runtimesRef.current.get(originKey);
        if (provisional) {
          runtimesRef.current.delete(originKey);
          runtimesRef.current.set(sessionId, provisional);
          setRuntimeSnapshot(new Map(runtimesRef.current));
        }
        abortRefs.current.delete(originKey);
        abortRefs.current.set(sessionId, abort);
        if (cancelRequestedRefs.current.delete(originKey)) {
          cancelRequestedRefs.current.add(sessionId);
        }
      }
      if (!originSessionId && navigationEpochRef.current === navigationEpoch) {
        activateConversation(sessionId);
      }
      setRecentChats((current) => [{
        id: sessionId as string,
        title: temporaryTitle,
        projectId: sessionProjectId,
        category: 'recent',
        generationStatus: 'generating',
      }, ...current.filter((chat) => chat.id !== sessionId && chat.id !== originKey)]);
      abortRefs.current.set(sessionId, abort);

      if (mailRequest) {
        mailSessionId = sessionId;
        const queueKey = `${sessionId}:${assistantId}`;
        let latestPersist = Promise.resolve();
        let lastPersistSignature = '';
        const persistProgress = (
          content: string,
          scan: MailScanProgress,
          activities: ChatActivity[],
        ) => {
          const turnStatus: ChatGenerationStatus = MAIL_TERMINAL_STATUSES.has(scan.status)
            ? scan.status === 'failed' ? 'failed' : 'completed'
            : 'generating';
          const signature = JSON.stringify({ scan, turnStatus, activities: activityWire(activities) });
          if (signature === lastPersistSignature) return;
          lastPersistSignature = signature;
          const previous = mailPersistQueueRef.current.get(queueKey) ?? Promise.resolve();
          latestPersist = previous.catch(() => undefined).then(() => persistMailScanTurn(
            sessionId as string,
            assistantId,
            text,
            turnStatus === 'generating' ? null : content,
            scan,
            abort.signal,
            { idempotencyKey, turnStatus, activities },
          ));
          mailPersistQueueRef.current.set(queueKey, latestPersist);
        };
        const initialScan: MailScanProgress = {
          status: 'connecting', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0,
        };
        persistProgress('', initialScan, mailActivities(initialScan));
        const result = await runMailScan(
          sessionId, assistantId, abort, mailCommandProviders(text), persistProgress
        );
        try {
          await latestPersist;
        } catch (persistErr) {
          console.warn('Failed to persist mail scan turn to chat history:', persistErr);
        } finally {
          if (mailPersistQueueRef.current.get(queueKey) === latestPersist) {
            mailPersistQueueRef.current.delete(queueKey);
          }
        }
        setApiStatus('online');
        const completedInBackground = activeConversationRef.current !== sessionId;
        const mailTurnStatus: ChatGenerationStatus = result.mailScan.status === 'failed'
          ? 'failed'
          : 'completed';
        updateRuntime(sessionId, (current) => ({ ...current,
          messages: current.messages.map((message) => message.id === assistantId
            ? { ...message, generationStatus: mailTurnStatus, isStreaming: false }
            : message),
        }));
        setChatStatus(sessionId, mailTurnStatus, completedInBackground && mailTurnStatus === 'completed');
        if (completedInBackground && mailTurnStatus === 'completed') window.dispatchEvent(new CustomEvent('chat-background-completed', {
          detail: { sessionId, title: temporaryTitle },
        }));
        void refreshHistory();
        return;
      }
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: sessionId,
            user_message: text,
            idempotency_key: idempotencyKey,
            document_ids: selectedAttachments
              .filter((item) => item.status === 'ready')
              .map((item) => item.documentId)
              .filter((documentId): documentId is string => Boolean(documentId)),
            reasoning_mode: reasoningMode,
          }),
          signal: abort.signal,
        }
      );
      if (cancelRequestedRefs.current.has(sessionId)) {
        await requestTurnCancellation(sessionId).catch(() => undefined);
        abort.abort();
        throw new DOMException('Chat cancellation requested.', 'AbortError');
      }
      let terminalStatus: ChatGenerationStatus | undefined;
      await parseSse(response, (event) => {
        if (event.event_type === 'started') {
          updateRuntime(sessionId as string, (current) => ({ ...current, status: 'generating',
            messages: current.messages.map((message) => message.id === assistantId
              ? { ...message, turnId: event.turn_id ?? message.turnId }
              : message),
          }));
        } else if (event.event_type === 'delta' && event.text) {
          updateRuntime(sessionId as string, (current) => ({ ...current,
            messages: current.messages.map((message) => message.id === assistantId
              ? { ...message, content: message.content + event.text }
              : message),
          }));
        } else if (event.event_type === 'activity') {
          const activities = activitiesFromPayload(event.activities);
          updateRuntime(sessionId as string, (current) => ({ ...current,
            messages: current.messages.map((message) => message.id === assistantId
              ? {
                  ...message,
                  activities,
                  completedAt: event.completed_at ?? message.completedAt,
                }
              : message),
          }));
        } else if (event.event_type === 'memory_citation') {
          const citation = citationFromEvent(event);
          if (citation) updateRuntime(sessionId as string, (current) => ({ ...current,
            messages: current.messages.map((message) => message.id === assistantId
              ? { ...message, citations: [...(message.citations ?? []), citation] }
              : message),
          }));
        } else if (event.event_type === 'completed') {
          terminalStatus = 'completed';
          const status = retrievalStatus(event.retrieval_status);
          const executionTrace = executionTraceFromPayload(event.execution_trace);
          const artifactRefs = artifactRefsFromPayload(event.artifact_refs);
          updateRuntime(sessionId as string, (current) => ({ ...current,
            messages: current.messages.map((message) => message.id === assistantId ? {
                  ...message,
                  ragEvidence: ragEvidenceFromPayload(event.rag_evidence),
                  retrievalStatus: status,
                  executionTrace: executionTrace ?? message.executionTrace,
                  artifactRefs: artifactRefs ?? message.artifactRefs,
                }
              : message),
          }));
        } else if (event.event_type === 'warning' && event.safe_message) {
          updateRuntime(sessionId as string, (current) => ({ ...current,
            messages: current.messages.map((message) => message.id === assistantId ? {
                  ...message,
                  content: [message.content, `⚠ ${event.safe_message}`]
                    .filter(Boolean)
                    .join('\n\n'),
                }
              : message),
          }));
        } else if (event.event_type === 'task_proposal' && event.proposal) {
          const workflow = workflowFromTaskProposal(event.proposal);
          if (workflow) {
            setWorkflows((current) => ({
              ...current,
              [workflow.taskId]: workflow,
            }));
            updateRuntime(sessionId as string, (current) => ({
              ...current,
              messages: current.messages.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      taskId: workflow.taskId,
                      taskStatus: 'AWAITING_PLAN_APPROVAL',
                    }
                  : message
              ),
            }));
          }
        } else if (event.event_type === 'error' && event.safe_message) {
          terminalStatus = generationStatus(event.status) ??
            (/usage/i.test(event.error_code ?? event.code ?? '') ? 'usage_limit_reached'
              : /rate/i.test(event.error_code ?? event.code ?? '') ? 'temporarily_rate_limited' : 'failed');
          updateRuntime(sessionId as string, (current) => ({ ...current,
            messages: current.messages.map((message) => message.id === assistantId ? {
                  ...message,
                  content: [message.content, event.safe_message].filter(Boolean).join('\n\n'),
                  errorCode: event.error_code ?? event.code,
                }
              : message),
          }));
        }
      });
      const completedStatus = terminalStatus ?? 'completed';
      updateRuntime(sessionId, (current) => ({ ...current, status: completedStatus,
        messages: current.messages.map((message) => message.id === assistantId
          ? { ...message, isStreaming: false, generationStatus: completedStatus }
          : message),
      }));
      setChatStatus(
        sessionId,
        completedStatus,
        completedStatus === 'completed' && activeConversationRef.current !== sessionId,
      );
      if (completedStatus === 'completed' && activeConversationRef.current !== sessionId) {
        window.dispatchEvent(new CustomEvent('chat-background-completed', {
          detail: { sessionId, title: temporaryTitle },
        }));
      }
      setApiStatus('online');
      updateRuntime(sessionId, (current) => ({
        ...current, selectedAttachments: [], attachmentError: null,
      }));
      void refreshHistory();
    } catch (cause) {
      if ((cause as { name?: string }).name === 'AbortError') {
        const targetKey = sessionId ?? originKey;
        {
          updateRuntime(targetKey, (current) => ({ ...current, status: 'cancelled',
            messages: current.messages.map((message) => message.id === assistantId ? {
                ...message,
                content: [message.content, 'Đã hủy.'].filter(Boolean).join('\n\n'),
                isStreaming: false,
                generationStatus: 'cancelled',
                activities: stopActivities(message.activities, 'cancelled'),
              }
              : message),
          }));
          if (sessionId) setChatStatus(sessionId, 'cancelled');
          else setRecentChats((current) => current.map((chat) => chat.id === originKey
            ? { ...chat, generationStatus: 'cancelled' }
            : chat));
        }
        if (mailRequest && sessionId) {
          const cancelledMessage = runtimeFor(sessionId).messages.find(
            (message) => message.id === assistantId,
          );
          const cancelledScan = cancelledMessage?.mailScan ?? {
            status: 'connecting' as const,
            emailsMatched: 0,
            emailsProcessed: 0,
            emailsToProcess: 0,
          };
          const cancelledActivities = stopActivities(
            cancelledMessage?.activities ?? mailActivities(cancelledScan),
            'cancelled',
          ) ?? [];
          const queueKey = `${sessionId}:${assistantId}`;
          const previous = mailPersistQueueRef.current.get(queueKey) ?? Promise.resolve();
          const cancelledWrite = previous.catch(() => undefined).then(() => persistMailScanTurn(
            sessionId as string,
            assistantId,
            text,
            null,
            cancelledScan,
            new AbortController().signal,
            { idempotencyKey, turnStatus: 'cancelled', activities: cancelledActivities },
          ));
          mailPersistQueueRef.current.set(queueKey, cancelledWrite);
          void cancelledWrite.finally(() => {
            if (mailPersistQueueRef.current.get(queueKey) === cancelledWrite) {
              mailPersistQueueRef.current.delete(queueKey);
            }
          }).catch(() => undefined);
        }
      } else {
        const error = cause instanceof Error ? cause.message : 'Chat backend unavailable.';
        const failedMailScan = isMailCommand(text);
        const mailScan = failedMailScan
          ? {
              status: 'failed' as const,
              emailsMatched: 0,
              emailsProcessed: 0,
              emailsToProcess: 0,
            }
          : undefined;
        const status: ChatGenerationStatus = /usage.?limit/i.test(error)
          ? 'usage_limit_reached'
          : /rate.?limit|too many requests|http 429/i.test(error)
            ? 'temporarily_rate_limited'
            : 'failed';
        const targetKey = sessionId ?? originKey;
        updateRuntime(targetKey, (current) => ({ ...current, status,
          messages: current.messages.map((message) => message.id === assistantId ? {
                ...message,
                content: error,
                isStreaming: false,
                mailScan: mailScan ?? message.mailScan,
                generationStatus: status,
                activities: failedMailScan
                  ? mailActivities(mailScan as MailScanProgress, message.activities)
                  : stopActivities(message.activities, 'failed'),
              }
            : message),
        }));
        if (failedMailScan && mailSessionId && mailScan) {
          void persistMailScanTurn(
            mailSessionId, assistantId, text, error, mailScan, abort.signal, {
              idempotencyKey,
              turnStatus: 'failed',
              activities: mailActivities(mailScan),
            }
          ).then(refreshHistory).catch(() => undefined);
        }
        if (sessionId) setChatStatus(sessionId, status);
        else setRecentChats((current) => current.map((chat) => chat.id === originKey
          ? { ...chat, generationStatus: status }
          : chat));
        setApiStatus('offline');
      }
    } finally {
      const abortKey = sessionId ?? originKey;
      if (abortRefs.current.get(abortKey) === abort) {
        abortRefs.current.delete(abortKey);
      }
      cancelRequestedRefs.current.delete(abortKey);
    }
  }, [activateConversation, ensureSession, persistMailScanTurn, projectId, reasoningMode, refreshHistory,
    requestTurnCancellation, runMailScan, runtimeFor, selectedAttachments, setChatStatus, updateRuntime]);

  const loadExistingChat = useCallback(async (sessionId: string, loadedProjectId?: string) => {
    const previousId = activeConversationRef.current;
    const previousMessages = previousId
      ? runtimesRef.current.get(previousId)?.messages
      : undefined;
    if (previousId && previousMessages && previousMessages.length > 0) {
      rememberTranscript(previousId, previousMessages);
    }
    if (loadedProjectId && loadedProjectId !== projectId) {
      pendingProjectChatRef.current = { projectId: loadedProjectId, sessionId };
    }
    navigationEpochRef.current += 1;
    loadHistoryAbortRef.current?.abort();
    const abort = new AbortController();
    loadHistoryAbortRef.current = abort;
    activateConversation(sessionId);
    setRecentChats((current) => current.map((chat) => chat.id === sessionId
      ? { ...chat, unread: false }
      : chat));
    const live = runtimesRef.current.get(sessionId);
    if (live?.status === 'generating') {
      setIsTranscriptLoading(false);
      return;
    }
    const cached = live?.messages.length ? live.messages : transcriptCacheRef.current.get(sessionId);
    if (cached) {
      updateRuntime(sessionId, (current) => ({ ...current, messages: cached }));
      setIsTranscriptLoading(false);
    } else {
      updateRuntime(sessionId, (current) => ({ ...current, messages: [] }));
      setIsTranscriptLoading(true);
    }
    try {
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
        { credentials: 'include', signal: abort.signal }
      );
      if (!response.ok) throw new Error(`Could not load chat (HTTP ${response.status}).`);
      const payload = (await response.json()) as { turns: ChatTurn[] };
      if (abort.signal.aborted) return;
      const next = messagesFromTurns(payload.turns);
      const turnStatus = payload.turns.at(-1)?.status;
      const loadedStatus = generationStatus(turnStatus);
      updateRuntime(sessionId, (current) => ({
        ...current,
        messages: next,
        status: (loadedStatus === 'generating' ? 'interrupted' : loadedStatus) ??
          (payload.turns.at(-1)?.assistant_message === null ? 'interrupted' : current.status),
      }));
      rememberTranscript(sessionId, next);
    } catch (err) {
      if ((err as { name?: string }).name === 'AbortError') return;
      throw err;
    } finally {
      if (loadHistoryAbortRef.current === abort) {
        setIsTranscriptLoading(false);
      }
    }
  }, [activateConversation, projectId, rememberTranscript, updateRuntime]);

  const loadFullEvidence = useCallback(async (chunkId: string): Promise<ChatRagEvidence | null> => {
    const guard = loadHistoryAbortRef.current;
    const sessionId = activeConversationId;
    if (!sessionId) return null;
    const response = await fetch(
      `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/messages?include_content=true`,
      { credentials: 'include' },
    );
    if (!response.ok) return null;
    const payload = (await response.json()) as { turns: ChatTurn[] };
    const next = messagesFromTurns(payload.turns);
    rememberTranscript(sessionId, next);
    if (loadHistoryAbortRef.current === guard) {
      updateRuntime(sessionId, (current) => ({ ...current, messages: next }));
    }
    for (const message of next) {
      const found = message.ragEvidence?.find((item) => item.chunkId === chunkId);
      if (found) return found;
    }
    return null;
  }, [activeConversationId, rememberTranscript, updateRuntime]);

  const prefetchChat = useCallback(async (sessionId: string) => {
    if (!sessionId || sessionId === activeConversationId) return;
    if (transcriptCacheRef.current.has(sessionId)) return;
    if (prefetchInFlightRef.current.has(sessionId)) return;
    if (prefetchInFlightRef.current.size >= 2) return;
    prefetchInFlightRef.current.add(sessionId);
    try {
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
        { credentials: 'include' },
      );
      if (!response.ok) return;
      const payload = (await response.json()) as { turns: ChatTurn[] };
      rememberTranscript(sessionId, messagesFromTurns(payload.turns));
    } catch {
      return;
    } finally {
      prefetchInFlightRef.current.delete(sessionId);
    }
  }, [activeConversationId, rememberTranscript]);

  const resetChat = useCallback(() => {
    navigationEpochRef.current += 1;
    loadHistoryAbortRef.current?.abort();
    activateNewDraft();
    setIsTranscriptLoading(false);
  }, [activateNewDraft]);

  const deleteChat = useCallback(async (sessionId: string) => {
    if (runtimesRef.current.get(sessionId)?.status === 'generating') return false;
    const response = await fetch(
      `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE' }
    );
    if (!response.ok) {
      throw new Error(`Could not delete chat (HTTP ${response.status}).`);
    }
    transcriptCacheRef.current.delete(sessionId);
    runtimesRef.current.delete(sessionId);
    abortRefs.current.delete(sessionId);
    setRecentChats((current) => current.filter((chat) => chat.id !== sessionId));
    if (activeConversationId === sessionId) {
      activateConversation(null);
      setIsTranscriptLoading(false);
    }
    await refreshHistory();
    return true;
  }, [activateConversation, activeConversationId, refreshHistory]);

  const stopGeneration = useCallback(() => {
    const sessionId = activeConversationRef.current;
    const runtimeKey = sessionId ?? draftKeyRef.current;
    if (runtimesRef.current.get(runtimeKey)?.status !== 'generating') return;
    cancelRequestedRefs.current.add(runtimeKey);
    if (sessionId) {
      void requestTurnCancellation(sessionId).finally(() => {
        abortRefs.current.get(sessionId)?.abort();
      });
    }
  }, [requestTurnCancellation]);

  const retryTurn = useCallback((messageId: string) => {
    const index = messages.findIndex((message) => message.id === messageId);
    const previous = messages.slice(0, index).findLast((message) => message.role === 'user');
    const assistant = messages[index];
    if (previous && assistant?.role === 'assistant') void sendMessage(previous.content, undefined, {
      assistantId: assistant.id,
      idempotencyKey: assistant.idempotencyKey ?? `turn_${assistant.id}`,
    });
  }, [messages, sendMessage]);

  const refreshWorkflow = useCallback(async (taskId: string) => { void taskId; }, []);
  const approveWorkflowPlan = useCallback(
    async (taskId: string) => {
      const sessionId = activeConversationId;
      if (!sessionId) return;
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/task-episodes/${encodeURIComponent(taskId)}/approve`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        }
      );
      if (!response.ok) {
        throw new Error(`Could not approve plan (HTTP ${response.status}).`);
      }
      setWorkflows((current) => {
        const existing = current[taskId];
        if (!existing || !existing.detail) return current;
        const updatedSteps: StepView[] = existing.detail.steps.map((step) => ({
          ...step,
          state: 'SUCCEEDED' as const,
        }));
        return {
          ...current,
          [taskId]: {
            ...existing,
            phase: 'Plan đã được phê duyệt — đang thực hiện.',
            detail: {
              ...existing.detail,
              task: {
                ...existing.detail.task,
                status: 'COMPLETED',
              },
              plan: existing.detail.plan
                ? {
                    ...existing.detail.plan,
                    approved: true,
                    steps: updatedSteps,
                  }
                : null,
              steps: updatedSteps,
            },
          },
        };
      });
    },
    [activeConversationId]
  );
  const reviseWorkflowPlan = useCallback(
    async (taskId: string, feedback: string) => {
      const sessionId = activeConversationId;
      if (!sessionId) return;
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/task-episodes/${encodeURIComponent(taskId)}/reject`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        }
      );
      if (!response.ok) {
        throw new Error(`Could not reject plan (HTTP ${response.status}).`);
      }
      setWorkflows((current) => {
        const existing = current[taskId];
        if (!existing || !existing.detail) return current;
        return {
          ...current,
          [taskId]: {
            ...existing,
            phase: 'Đã gửi yêu cầu chỉnh sửa kế hoạch.',
            detail: {
              ...existing.detail,
              task: {
                ...existing.detail.task,
                status: 'CANCELLED',
              },
              plan: existing.detail.plan
                ? {
                    ...existing.detail.plan,
                    approved: false,
                  }
                : null,
            },
          },
        };
      });
      if (feedback.trim()) {
        await sendMessage(`Chỉnh sửa kế hoạch: ${feedback.trim()}`);
      }
    },
    [activeConversationId, sendMessage]
  );
  const retryWorkflowStep = useCallback(
    async (taskId: string, stepId: string) => { void taskId; void stepId; },
    []
  );

  return {
    workflows,
    refreshWorkflow,
    approveWorkflowPlan,
    reviseWorkflowPlan,
    retryWorkflowStep,
    retryTurn,
    pendingClarificationTurnId: null,
    messages,
    recentChats,
    isHistoryLoading: isSessionListLoading,
    isSessionListLoading,
    isTranscriptLoading,
    activeConversationId,
    isGenerating,
    inputText,
    setInputText,
    selectedAttachments,
    attachmentError,
    selectAttachments,
    removeAttachment,
    sendMessage,
    stopGeneration,
    resetChat,
    deleteChat,
    loadExistingChat,
    loadFullEvidence,
    prefetchChat,
    apiStatus,
  };
}
