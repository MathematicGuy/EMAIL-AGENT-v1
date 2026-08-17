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
  listConnections,
  newIdempotencyKey,
  type DigestRunView,
  type DigestTask,
} from '../../modules/mail/api';
import type {
  ChatCitation,
  ChatComposerAttachment,
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
}

interface SseEvent {
  event_type: string;
  text?: string;
  code?: string;
  safe_message?: string;
  source_id?: string;
  citation_scope?: string;
  project_id?: string;
  document_id?: string;
  document_title?: string;
  section?: string;
  page_start?: number;
  page_end?: number;
  rag_evidence?: Array<Record<string, unknown>>;
  retrieval_status?: string;
}

const HISTORY_CACHE_LIMIT = 20;
const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;
const ACCEPTED_FILE_EXTENSIONS = new Set(['docx', 'pdf']);
const MAIL_COMMAND = /(?:^|\s)@mail\b/i;
const MAIL_UNREAD_QUERY = 'is:unread in:inbox category:primary';
const MAIL_SCAN_MAX_EMAILS = 10;
const MAIL_POLL_INTERVAL_MS = 1_500;
const MAIL_TERMINAL_STATUSES = new Set(['succeeded', 'partial', 'failed']);

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

function rememberTranscript(
  cache: Map<string, ChatMessage[]>,
  sessionId: string,
  transcript: ChatMessage[],
): void {
  cache.delete(sessionId);
  cache.set(sessionId, transcript);
  while (cache.size > HISTORY_CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
}

function messagesFromTurns(turns: ChatTurn[]): ChatMessage[] {
  return turns.flatMap((turn) => {
    const citations = (turn.citation_coordinates ?? [])
      .map(citationFromCoordinate)
      .filter((item): item is ChatCitation => item !== null);
    const hasStoredEvidence = Array.isArray(turn.rag_evidence);
    const ragEvidence = hasStoredEvidence ? ragEvidenceFromPayload(turn.rag_evidence) : undefined;
    const status = retrievalStatus(turn.retrieval_status);
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
      },
    ];
  });
}

function isMailCommand(value: string): boolean {
  return MAIL_COMMAND.test(value);
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
      typeof (item as Record<string, unknown>).preview !== 'string' ||
      typeof (item as Record<string, unknown>).content !== 'string'
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
      content: evidence.content as string,
    }];
  }).slice(0, 5);
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
  projectIds: string[] = projectId ? [projectId] : []
) {
  void modelId;
  void projectIds;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [recentChats, setRecentChats] = useState<RecentChat[]>([]);
  const [isHistoryLoading, setIsHistoryLoading] = useState(true);
  const [isTranscriptLoading, setIsTranscriptLoading] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [inputText, setInputText] = useState('');
  const [selectedAttachments, setSelectedAttachments] = useState<ChatComposerAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<'unknown' | 'online' | 'offline'>('unknown');
  const [workflows] = useState<Record<string, TaskWorkflow>>({});
  const abortRef = useRef<AbortController | null>(null);
  const loadHistoryAbortRef = useRef<AbortController | null>(null);
  const attachmentPollsRef = useRef(new Map<string, AbortController>());
  const historyCacheRef = useRef(new Map<string, ChatMessage[]>());
  const messagesRef = useRef<ChatMessage[]>([]);
  const activeConversationIdRef = useRef<string | null>(null);
  messagesRef.current = messages;
  activeConversationIdRef.current = activeConversationId;

  const refreshHistory = useCallback(async () => {
    if (!projectId) {
      setRecentChats([]);
      setIsHistoryLoading(false);
      return;
    }
    setIsHistoryLoading(true);
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
      setRecentChats(payload.sessions.map((session, index) => ({
        id: session.session_id,
        title: session.title ?? `Chat ${payload.sessions.length - index}`,
        projectId: session.project_id,
        category: 'recent',
      })));
      setApiStatus('online');
    } catch {
      setApiStatus('offline');
      setRecentChats([]);
    } finally {
      setIsHistoryLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    queueMicrotask(() => {
      setMessages([]);
      setActiveConversationId(null);
      setSelectedAttachments([]);
      void refreshHistory();
    });
  }, [projectId, refreshHistory]);

  useEffect(() => () => {
    for (const controller of attachmentPollsRef.current.values()) controller.abort();
    attachmentPollsRef.current.clear();
  }, [projectId]);

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
    setActiveConversationId(payload.session_id);
    return payload.session_id;
  }, [activeConversationId, projectId]);

  const selectAttachments = useCallback((files: File[]) => {
    if (!projectId) {
      setAttachmentError('Select a Project before uploading documents.');
      return;
    }
    window.dispatchEvent(new CustomEvent('open-project-documents'));
    for (const file of files) {
      const validation = validateAttachmentFile(file);
      const id = `upload_${crypto.randomUUID?.() ?? Date.now()}`;
      if (validation) {
        setAttachmentError(validation);
        continue;
      }
      const pollController = new AbortController();
      attachmentPollsRef.current.set(id, pollController);
      setSelectedAttachments((current) => [...current, {
        id,
        name: file.name,
        mediaType: file.type,
        sizeBytes: file.size,
        status: 'hashing',
      }]);
      void uploadProjectDocument(projectId, file, (status) => {
        if (pollController.signal.aborted) return;
        setSelectedAttachments((current) => current.map((item) =>
          item.id === id ? { ...item, status } : item
        ));
      }, pollController.signal).then(async (document) => {
        if (pollController.signal.aborted) return;
        setSelectedAttachments((current) => current.map((item) =>
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
        setSelectedAttachments((current) => current.map((item) =>
          item.id === id ? {
            ...item,
            status: finished.status === 'ready' ? 'ready' : 'error',
            error: finished.status === 'failed'
              ? (finished.error_code ?? 'Document processing failed.')
              : undefined,
          } : item
        ));
        setAttachmentError(null);
        window.dispatchEvent(new CustomEvent('project-documents-updated'));
      }).catch((cause) => {
        if (pollController.signal.aborted) return;
        const message = cause instanceof Error ? cause.message : 'Upload failed.';
        setSelectedAttachments((current) => current.map((item) =>
          item.id === id ? { ...item, status: 'error', error: message } : item
        ));
        setAttachmentError(message);
      }).finally(() => {
        if (attachmentPollsRef.current.get(id) === pollController) {
          attachmentPollsRef.current.delete(id);
        }
      });
    }
  }, [projectId]);

  const removeAttachment = useCallback((attachmentId: string) => {
    attachmentPollsRef.current.get(attachmentId)?.abort();
    attachmentPollsRef.current.delete(attachmentId);
    setSelectedAttachments((current) => current.filter((item) => item.id !== attachmentId));
  }, []);

  const runMailScan = useCallback(async (
    assistantId: string,
    abort: AbortController,
  ): Promise<{ content: string; mailScan: MailScanProgress }> => {
    const updateAssistant = (
      content: string,
      mailScan: MailScanProgress,
      isStreaming = true,
    ) => {
      setMessages((current) => current.map((message) =>
        message.id === assistantId ? { ...message, content, mailScan, isStreaming } : message
      ));
    };
    const activeConnections = (await listConnections(abort.signal))
      .filter((connection) => connection.status === 'active');
    if (!activeConnections[0]) {
      const mailScan = {
        status: 'failed' as const, emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0,
      };
      const content = 'Chưa có tài khoản Gmail đang kết nối. Hãy mở Mail Inbox để kết nối Gmail.';
      updateAssistant(content, mailScan, false);
      return { content, mailScan };
    }
    updateAssistant('Đã kết nối Gmail. Đang tạo lượt quét 10 email unread mới nhất…', {
      status: 'connecting', emailsMatched: 0, emailsProcessed: 0, emailsToProcess: 0,
    });
    const accepted = await createDigestRun({
      mailboxConnectionId: activeConnections[0].id,
      maxEmails: MAIL_SCAN_MAX_EMAILS,
      query: MAIL_UNREAD_QUERY,
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
        if (consecutiveErrors >= 5) {
          throw err;
        }
        await waitForMailPoll(abort.signal);
        continue;
      }
      const progress = mailScanProgress(run);
      if (!MAIL_TERMINAL_STATUSES.has(run.status)) {
        updateAssistant('Đang quét 10 email unread mới nhất…', progress);
        await waitForMailPoll(abort.signal);
        continue;
      }
      if (run.status === 'failed') {
        const message = run.error?.message ?? 'Không thể hoàn tất lượt quét email.';
        updateAssistant(message, progress, false);
        return { content: message, mailScan: progress };
      }
      let tasks: DigestTask[];
      try {
        tasks = await getDigestTasks(run.id, abort.signal);
      } catch (err) {
        if ((err as { name?: string }).name === 'AbortError') throw err;
        // Fallback gracefully if task details retrieval had a blip
        tasks = [];
      }
      const completedProgress = { ...progress, actionItemsCount: tasks.length || run.progress.actionItemsCount || 0 };
      const resultLabel = run.status === 'partial' ? 'Hoàn tất một phần' : 'Đã quét xong';
      const scannedSummary = run.progress.emailsMatched > run.progress.emailsProcessed
        ? `đã xử lý ${run.progress.emailsProcessed}/${run.progress.emailsMatched} email phù hợp`
        : `đã quét ${run.progress.emailsProcessed} email`;
      const finalCount = tasks.length || run.progress.actionItemsCount || 0;
      const filteredSummary = run.progress.filteredSummary?.trim();
      const content = [
        `${resultLabel}: ${scannedSummary} và tạo ${finalCount} action item.`,
        filteredSummary,
      ].filter(Boolean).join('\n\n');
      updateAssistant(
        content,
        completedProgress,
        false,
      );
      return {
        content,
        mailScan: completedProgress,
      };
    }
    throw new DOMException('Mail scan polling aborted', 'AbortError');
  }, []);

  const persistMailScanTurn = useCallback(async (
    sessionId: string,
    turnId: string,
    userMessage: string,
    assistantMessage: string,
    mailScan: MailScanProgress,
    signal: AbortSignal,
  ) => {
    let targetSessionId = sessionId;
    let response = await fetch(
      `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(targetSessionId)}/mail-scans`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          turn_id: turnId,
          user_message: userMessage,
          assistant_message: assistantMessage,
          mail_scan: {
            status: mailScan.status,
            emails_matched: mailScan.emailsMatched,
            emails_processed: mailScan.emailsProcessed,
            emails_to_process: mailScan.emailsToProcess,
            action_items_count: mailScan.actionItemsCount ?? null,
          },
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
          setActiveConversationId(newSession.session_id);
          response = await fetch(
            `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(targetSessionId)}/mail-scans`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                turn_id: turnId,
                user_message: userMessage,
                assistant_message: assistantMessage,
                mail_scan: {
                  status: mailScan.status,
                  emails_matched: mailScan.emailsMatched,
                  emails_processed: mailScan.emailsProcessed,
                  emails_to_process: mailScan.emailsToProcess,
                  action_items_count: mailScan.actionItemsCount ?? null,
                },
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
    }
  }, [projectId]);

  const sendMessage = useCallback(async (override?: string, projectIdOverride?: string) => {
    const text = (override ?? inputText).trim();
    if (!text || isGenerating) return;
    if (selectedAttachments.some((item) => item.status !== 'ready')) {
      setAttachmentError(
        navigator.language.toLowerCase().startsWith('vi')
          ? 'Tài liệu đang được xử lý. Hãy chờ hoặc gỡ tài liệu để gửi tin nhắn.'
          : 'A selected document is still processing. Wait or remove it before sending.'
      );
      return;
    }
    setInputText('');
    setIsGenerating(true);
    const now = Date.now();
    const userMessage: ChatMessage = {
      id: `user-${now}`,
      role: 'user',
      content: text,
      timestamp: timestamp(),
    };
    const assistantId = `assistant-${now}`;
    setMessages((current) => [...current, userMessage, {
      id: assistantId,
      role: 'assistant',
      content: '',
      timestamp: timestamp(),
      isStreaming: true,
    }]);
    const abort = new AbortController();
    let mailSessionId: string | null = null;
    abortRef.current = abort;
    try {
      if (isMailCommand(text)) {
        const sessionId = await ensureSession(projectIdOverride);
        mailSessionId = sessionId;
        const result = await runMailScan(assistantId, abort);
        try {
          await persistMailScanTurn(
            sessionId, assistantId, text, result.content, result.mailScan, abort.signal
          );
        } catch (persistErr) {
          console.warn('Failed to persist mail scan turn to chat history:', persistErr);
        }
        setApiStatus('online');
        void refreshHistory();
        return;
      }
      const sessionId = await ensureSession(projectIdOverride);
      const response = await fetch(
        `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            session_id: sessionId,
            user_message: text,
            idempotency_key: `turn_${crypto.randomUUID?.() ?? now}`,
            document_ids: selectedAttachments
              .filter((item) => item.status === 'ready')
              .map((item) => item.documentId)
              .filter((documentId): documentId is string => Boolean(documentId)),
          }),
          signal: abort.signal,
        }
      );
      await parseSse(response, (event) => {
        if (event.event_type === 'delta' && event.text) {
          setMessages((current) => current.map((message) =>
            message.id === assistantId
              ? { ...message, content: message.content + event.text }
              : message
          ));
        } else if (event.event_type === 'memory_citation') {
          const citation = citationFromEvent(event);
          if (citation) setMessages((current) => current.map((message) =>
            message.id === assistantId
              ? { ...message, citations: [...(message.citations ?? []), citation] }
              : message
          ));
        } else if (event.event_type === 'completed') {
          const status = retrievalStatus(event.retrieval_status);
          if (status || Array.isArray(event.rag_evidence)) {
            setMessages((current) => current.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    ragEvidence: ragEvidenceFromPayload(event.rag_evidence),
                    retrievalStatus: status,
                  }
                : message
            ));
          }
        } else if (event.event_type === 'warning' && event.safe_message) {
          setMessages((current) => current.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  content: [message.content, `⚠ ${event.safe_message}`]
                    .filter(Boolean)
                    .join('\n\n'),
                }
              : message
          ));
        } else if (event.event_type === 'error' && event.safe_message) {
          setMessages((current) => current.map((message) =>
            message.id === assistantId
              ? {
                  ...message,
                  content: [message.content, event.safe_message].filter(Boolean).join('\n\n'),
                }
              : message
          ));
        }
      });
      setMessages((current) => current.map((message) =>
        message.id === assistantId ? { ...message, isStreaming: false } : message
      ));
      setApiStatus('online');
      setSelectedAttachments([]);
      setAttachmentError(null);
      void refreshHistory();
    } catch (cause) {
      if ((cause as { name?: string }).name === 'AbortError') {
        setMessages((current) => current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: [message.content, 'Chat interrupted.'].filter(Boolean).join('\n\n'),
                isStreaming: false,
              }
            : message
        ));
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
        setMessages((current) => current.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                content: error,
                isStreaming: false,
                mailScan: mailScan ?? message.mailScan,
              }
            : message
        ));
        if (failedMailScan && mailSessionId && mailScan) {
          void persistMailScanTurn(
            mailSessionId, assistantId, text, error, mailScan, abort.signal
          ).then(refreshHistory).catch(() => undefined);
        }
        setApiStatus('offline');
      }
    } finally {
      abortRef.current = null;
      setIsGenerating(false);
    }
  }, [ensureSession, inputText, isGenerating, persistMailScanTurn, refreshHistory, runMailScan, selectedAttachments]);

  const loadExistingChat = useCallback(async (sessionId: string, loadedProjectId?: string) => {
    void loadedProjectId;
    const previousId = activeConversationIdRef.current;
    if (previousId && messagesRef.current.length > 0) {
      rememberTranscript(historyCacheRef.current, previousId, messagesRef.current);
    }
    loadHistoryAbortRef.current?.abort();
    const abort = new AbortController();
    loadHistoryAbortRef.current = abort;
    setActiveConversationId(sessionId);
    const cached = historyCacheRef.current.get(sessionId);
    if (cached) {
      setMessages(cached);
      setIsTranscriptLoading(false);
    } else {
      setMessages([]);
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
      setMessages(next);
      rememberTranscript(historyCacheRef.current, sessionId, next);
    } catch (err) {
      if ((err as { name?: string }).name === 'AbortError') return;
      throw err;
    } finally {
      if (loadHistoryAbortRef.current === abort) {
        setIsTranscriptLoading(false);
      }
    }
  }, []);

  const resetChat = useCallback(() => {
    abortRef.current?.abort();
    loadHistoryAbortRef.current?.abort();
    setMessages([]);
    setActiveConversationId(null);
    setIsGenerating(false);
  }, []);

  const deleteChat = useCallback(async (sessionId: string) => {
    if (isGenerating && activeConversationId === sessionId) return false;
    const response = await fetch(
      `${API_BASE_URL}/v1/cowork/chat/sessions/${encodeURIComponent(sessionId)}`,
      { method: 'DELETE' }
    );
    if (!response.ok) {
      throw new Error(`Could not delete chat (HTTP ${response.status}).`);
    }
    historyCacheRef.current.delete(sessionId);
    if (activeConversationId === sessionId) {
      setMessages([]);
      setActiveConversationId(null);
      setSelectedAttachments([]);
    }
    await refreshHistory();
    return true;
  }, [activeConversationId, isGenerating, refreshHistory]);

  const stopGeneration = useCallback(() => {
    abortRef.current?.abort();
    setIsGenerating(false);
  }, []);

  const retryTurn = useCallback((messageId: string) => {
    const index = messages.findIndex((message) => message.id === messageId);
    const previous = messages.slice(0, index).findLast((message) => message.role === 'user');
    if (previous) void sendMessage(previous.content);
  }, [messages, sendMessage]);

  const refreshWorkflow = useCallback(async (taskId: string) => { void taskId; }, []);
  const approveWorkflowPlan = useCallback(async (taskId: string) => { void taskId; }, []);
  const reviseWorkflowPlan = useCallback(
    async (taskId: string, feedback: string) => { void taskId; void feedback; },
    []
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
    isHistoryLoading,
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
    apiStatus,
  };
}
